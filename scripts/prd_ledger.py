#!/usr/bin/env python3
"""
The PRD requirement ledger: one authored table, three generated artifacts.

    python scripts/prd_ledger.py                 # regenerate both requirement sets
    python scripts/prd_ledger.py --set v7        # regenerate one set
    python scripts/prd_ledger.py --check         # fail if a terminal row lacks evidence

Rows change through the PROGRESS lists below, never by editing the audited
rows in place.

Requirement sets
----------------
  v1  PRD v1.0 Universal Product Baseline (`REQUIREMENTS`, this file)
  v7  PRD v7.0 (`docs/PRD_V7.md` section 91; rows in `prd_v7_table.py`). Each
      v7 row names the v1 rows whose evidence it reuses (`maps_to_v1`);
      `--check` fails on a link to a v1 row that does not exist.

Artifacts
---------
  data/prd_requirements.json                      v1 machine-readable ledger
  docs/implementation/PRD_REQUIREMENTS_LEDGER.md  v1 human-readable ledger
  data/prd_final_verdict.json                     v1 roll-up verdict
  data/prd_v7_requirements.json                   v7 machine-readable ledger
  docs/implementation/PRD_V7_LEDGER.md            v7 human-readable ledger
  data/prd_v7_verdict.json                        v7 roll-up verdict

Why the table lives in this file
--------------------------------
The requirements are extracted from `PRD.md` (v1.0 Universal Product
Baseline, sha256 1777465992d37628f8c1ec0cc44c9ccf8aada4f07357140b8bbb8687
b231bf35) plus the product owner's recorded direction of 2026-09-22, which
answers and in places amends the PRD. Keeping the table as authored Python
rather than a hand-edited JSON means a row cannot silently lose a field and
the renderer cannot invent one.

The status vocabulary is deliberately split. TERMINAL states are the only
ones the program may finish on; everything else is work in progress and the
roll-up refuses to call the program complete while any row holds one.

A rule the renderer enforces (`--check`): a row may not be VERIFIED without
non-empty `evidence`, and may not be BLOCKED_EXTERNAL without a `blocker`.
That is the only defence against a ledger that reads green because someone
typed green.
"""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# --- status vocabulary -------------------------------------------------------

VERIFIED = "VERIFIED"
BLOCKED_EXTERNAL = "BLOCKED_EXTERNAL"
DEFERRED_BY_PRD = "DEFERRED_BY_PRD"
REJECTED_WITH_EVIDENCE = "REJECTED_WITH_EVIDENCE"
NOT_APPLICABLE = "NOT_APPLICABLE"

TERMINAL = frozenset({VERIFIED, BLOCKED_EXTERNAL, DEFERRED_BY_PRD,
                      REJECTED_WITH_EVIDENCE, NOT_APPLICABLE})

# Working states. None of these may end the program.
PARTIAL = "PARTIAL"                      # implemented with a named gap
EXISTING_UNVERIFIED = "EXISTING_UNVERIFIED"  # code exists, no evidence on this tree
MISSING = "MISSING"                      # no implementation
IMPLEMENTING = "IMPLEMENTING"
TESTING = "TESTING"
NOT_AUDITED = "NOT_AUDITED"
# Added with PRD v7, whose section 0 classifies repository state in these
# terms too. All three still need work, so none of them may end the program.
BROKEN = "BROKEN"                        # implemented, demonstrably not working
DUPLICATED = "DUPLICATED"                # two implementations of one concern
SUPERSEDED = "SUPERSEDED"                # built to a contract v7 replaced

# The owner paused it: still owed and never terminal, but the scheduler leaves
# it alone until the owner resumes it (2026-10-02, CORE-001).
PARKED_BY_OWNER = "PARKED_BY_OWNER"

WORKING = frozenset({PARTIAL, EXISTING_UNVERIFIED, MISSING, IMPLEMENTING,
                     TESTING, NOT_AUDITED, BROKEN, DUPLICATED, SUPERSEDED,
                     PARKED_BY_OWNER})

ALL_STATES = TERMINAL | WORKING

P0, P1, P2, P3 = "P0", "P1", "P2", "P3"


def R(id_, section, summary, priority, *, status=NOT_AUDITED, deps=(),
      impl="", files=(), tests=(), evidence="", blocker="", next_action="",
      owner_note=""):
    """One requirement row. Every field is explicit; none is inferred.

    The single derivation: a working row with no next action of its own but
    with dependencies is carried by those dependencies, and the ledger says
    so rather than leaving the cell empty. A working row with neither is a
    ledger defect and `--check` reports it.
    """
    if status in WORKING and not next_action and deps:
        next_action = ("Carried by " + ", ".join(deps)
                       + "; this row is verified when they are.")
    return {
        "id": id_,
        "section": section,
        "summary": summary,
        "priority": priority,
        "dependencies": list(deps),
        "status": status,
        "current_implementation": impl,
        "files": list(files),
        "tests": list(tests),
        "evidence": evidence,
        "blocker": blocker,
        "next_action": next_action,
        "owner_direction": owner_note,
    }


def R7(id_, section, summary, priority, *, acceptance, maps_to_v1=(), **fields):
    """One PRD v7 row: a v1-shaped row plus the acceptance evidence section 91
    names for it and the v1 rows whose evidence it reuses (v7 section 0: reuse
    before rebuild, so an existing proof is linked, never re-invented)."""
    row = R(id_, section, summary, priority, **fields)
    row["acceptance"] = acceptance
    row["maps_to_v1"] = list(maps_to_v1)
    return row


def _load_v7() -> list[dict]:
    """The v7 rows live in their own file so this one does not double in size;
    they are built with R7 here, so the same field guarantees hold."""
    path = Path(__file__).with_name("prd_v7_table.py")
    spec = importlib.util.spec_from_file_location("prd_v7_table", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.table(R7)


# --- the table ---------------------------------------------------------------
# Status here is the AUDITED state as of the audit that wrote it; the
# implementation program updates rows through --status/--evidence so the
# history stays in git rather than in someone's memory.

REQUIREMENTS = [

    # ---------- 9. Installation and first run ----------
    R("FR-ONB-001", "9", "One installer provisions the runtime on a clean Windows system without manual cloning or venv creation; failures name the component and offer repair; updates never erase user state.", P1,
      status=PARTIAL,
      impl="FRIDAY.exe onedir bundle + launcher (roles/supervisor/doctor/paths), state outside the bundle, legacy-state migration, distribution zip. No provisioning of Hermes, a browser runtime or a coding CLI.",
      files=["friday/launcher.py", "scripts/build_windows.py", "friday/config.py", "packaging/"],
      tests=["tests/test_launcher.py", "tests/test_launcher_doctor.py", "tests/test_state_root.py", "scripts/validate_exe.py", "scripts/validate_exe_clean.py"],
      evidence="docs/build/FRIDAY_EXE_VALIDATION.md: golden journeys 15/15, clean-machine 6/6 on build 5 (2026-09-16).",
      next_action="Add a provisioning stage that installs/locates Hermes, the browser runtime and the coding CLI, each with its own PASS/DEGRADED/FAIL and a repair path.",
      owner_note="Owner: 'everything should be automatically autonomous - Friday, Hermes, browser runtime, Claude Code, connect everything, done.'"),
    R("FR-ONB-002", "9", "Install may need internet; after setup FRIDAY starts and stays useful during an outage, and cloud-dependent actions fail honestly without corrupting objectives.", P1,
      status=EXISTING_UNVERIFIED,
      impl="Provider health/cooldown, provider fallback, objective failure kinds incl. CONNECTIVITY; doctor runs offline.",
      files=["friday/provider_health.py", "friday/provider_fallback.py", "friday/objectives.py", "friday/launcher.py"],
      tests=["tests/test_provider_outage.py", "tests/test_provider_fallback.py"],
      next_action="Add an offline-start acceptance test: no network, FRIDAY starts, local capabilities work, a cloud action fails as CONNECTIVITY and the objective survives.",
      owner_note="Owner: internet-free installation is not required."),
    R("FR-ONB-003", "9", "Useful baseline on 8 GB RAM / 4 cores / no GPU; the installer picks compatible defaults and reports unavailable high-end capability as degraded rather than failing.", P1,
      status=PARTIAL,
      impl="Resource governor caps concurrent workers and sheds under RAM pressure; soak sampler records RSS/CPU. No installer hardware profile.",
      files=["friday/governor.py", "scripts/soak.py", "friday/resilience.py"],
      tests=["tests/test_governor.py", "tests/test_soak.py"],
      evidence="Governor refused a live delegation at 96% RAM (PRD v3.1 matrix NFR-P09).",
      next_action="Add hardware detection at first run that selects a profile (local model on/off, concurrency, heavy features) and records the choice."),
    R("FR-ONB-004", "9", "Progressive conversational onboarding learns identity, profession, business, work, apps, sites, goals, model accounts, privacy, autonomy, languages, hours, folders, recurring tasks and team; useful in 5-10 minutes; produces inspectable memory and policy.", P1,
      status=PARTIAL, deps=["FR-PRV-002"],
      impl="FirstRunContract: 9 adaptive questions persisted as an operating contract and folded into the versioned UserPolicy. Does not cover profession/company/apps/sites/languages/hours/folders/team, and does not write a workspace profile.",
      files=["friday/first_run.py", "friday/user_policy.py"],
      tests=["tests/test_first_run.py"],
      next_action="Extend to a staged interview that writes an inspectable workspace profile into canonical memory and compiles policy, with every question skippable and resumable."),
    R("FR-ONB-005", "9", "Environment discovery detects Chrome, dev tooling, audio devices, Git, coding CLIs and runtimes; never self-grants sensitive access; detected capability appears in the registry with real health.", P1,
      status=PARTIAL,
      impl="Doctor sections (application/database/Hermes/skills/browser/voice/providers/security), app map, health report, capability registry with health.",
      files=["friday/launcher.py", "friday/apps.py", "friday/os_map.py", "friday/health.py", "friday/capabilities.py"],
      tests=["tests/test_launcher_doctor.py", "tests/test_health.py"],
      next_action="Add discovery of coding CLIs (claude/codex/opencode) and Chrome profiles as registry entries with health, plus an approval prompt for sensitive access."),

    # ---------- 10. Browser-first control ----------
    R("FR-BRW-001", "10", "Integration selection hierarchy: safe native capability, authenticated browser, OAuth connector, official API when approved, human handoff; no platform API keys required for a normal web portal; Developer Mode records the chosen method and why.", P1,
      status=PARTIAL,
      impl="Capability router and executor router choose a route; browser capability classifies URLs; connector plane for OAuth-ish entry. No single recorded selection hierarchy with a reason per task.",
      files=["friday/capability_router.py", "friday/executor_router.py", "friday/browser_capability.py", "friday/connectors/plane.py"],
      tests=["tests/test_capability_router.py", "tests/test_executor_router.py", "tests/test_connector_plane.py"],
      next_action="Add an explicit integration-selection record (method, why, alternatives rejected) surfaced in Developer Mode.",
      owner_note="Owner: browser/OAuth first; API keys only if the user asks or the browser route fails."),
    R("FR-BRW-002", "10", "Supports installed Google Chrome; may attach to an approved profile or create a dedicated persistent FRIDAY profile; the user can see which profile a task uses; locked/unavailable sessions degrade gracefully.", P1,
      status=EXISTING_UNVERIFIED,
      impl="Chrome profile discovery and choice (worker/approval-driven), Playwright driver, profile lock handling.",
      files=["friday/browser_profiles.py", "friday/browser.py"],
      tests=["tests/test_browser_profiles.py", "tests/test_browser_primitives.py"],
      next_action="Re-verify on this tree and surface the profile in the activity/Developer Mode view.",
      owner_note="Owner: mostly the user's own Chrome profile; FRIDAY may create its own when needed."),
    R("FR-BRW-003", "10", "Users complete passwords, OTPs, passkeys and security keys through normal site flows; secrets never appear in cloud-model prompts or logs; auth flows pause and resume the objective; no silent bypass of security controls.", P0,
      status=EXISTING_UNVERIFIED, deps=["FR-SEC-001"],
      impl="Human-verification detection in page state, auth-boundary resume, secret broker handles, sensitive-domain blocking before capture.",
      files=["friday/browser.py", "friday/secret_broker.py", "friday/sensitive_domains.py"],
      tests=["tests/test_auth_boundary_resume.py", "tests/test_secret_broker.py", "tests/test_sensitive_domains.py"],
      next_action="Re-verify; add the captcha boundary explicitly (handoff by default, policy-gated adapter seam).",
      owner_note="Owner asked FRIDAY to solve captchas itself; OTP and hardware keys stay with the user. Recorded as a policy-gated seam - see PRD-OWN-CAPTCHA."),
    R("FR-BRW-004", "10", "A browser action may be claimed only after observing matching resulting state; a click is not proof; read-back is captured where practical; failed verification reports uncertainty.", P0,
      status=EXISTING_UNVERIFIED, deps=["FR-TRU-001"],
      impl="Browser observe/verify step loop, page-state read-back, action evidence ledger, honesty audit on claims.",
      files=["friday/browser.py", "friday/honesty.py", "friday/store.py"],
      tests=["tests/test_browser_primitives.py", "tests/test_action_chain.py", "tests/test_honesty.py"],
      next_action="Re-verify on this tree with a negative control (forged click success must be refused)."),

    # ---------- 11. Model and intelligence architecture ----------
    R("FR-MDL-001", "11", "Model capability registry with provider, model, auth, tool/vision capability, context, cost, latency, health, privacy class, preference and evaluation history; credentials alone never mark a route healthy; health needs semantically valid output; metadata changes without touching orchestration.", P0,
      impl="model_capabilities registry, provider health from evidence, provider diagnostics, route-level health and cooldowns.",
      files=["friday/model_capabilities.py", "friday/provider_health.py", "friday/provider_diagnostics.py", "friday/providers.py"],
      tests=["tests/test_model_capabilities.py", "tests/test_provider_health.py", "tests/test_provider_diagnostics.py"],
      next_action="",
      status='VERIFIED',
      evidence='tests/test_invariants.py health guards: credentials plus a transport success are not health, and a transport success with zero output is not health; provider guard: a foreign model never reaches a provider and every candidate route carries a model that provider owns. tests/test_invariants.py 28 passed incl. test_every_invariant_guard_is_load_bearing over 13 guards - each guard is removed in a subprocess and its attack test is required to go RED'),
    R("FR-MDL-002", "11", "Users may connect Claude, ChatGPT/Codex, Gemini, API providers, OpenAI-compatible endpoints and local models; FRIDAY works with one provider and with none (local/deterministic features).", P1,
      status=PARTIAL,
      impl="Hermes gateway multi-provider, provider registry, deterministic planner path works with zero model calls.",
      files=["friday/providers.py", "friday/model_gateway.py", "friday/hermes_bridge.py"],
      tests=["tests/test_providers.py", "tests/test_model_gateway.py"],
      evidence="Golden objective suite ran 162/162 with 0 model calls on the deterministic path (PRD v3.1 matrix).",
      next_action="Add consumer-friendly Connect actions (OAuth/browser sign-in) rather than raw config editing."),
    R("FR-MDL-003", "11", "Hermes is the multi-provider execution/model substrate, not the manager; FRIDAY requests inference through it without ceding policy authority and independently verifies consequential results.", P0,
      status=BLOCKED_EXTERNAL,
      impl="Hermes bridge (TaskBundle/WorkRunLog), model gateway worker, supervisor, team submit/poll.",
      files=["friday/hermes_bridge.py", "friday/hermes_model_gateway_worker.py", "friday/hermes_team.py", "friday/hermes_health.py"],
      tests=["tests/test_hermes_bridge.py", "tests/test_hermes_engine.py", "tests/test_model_gateway.py"],
      evidence="Deterministic tests use tests/fake_hermes_cli.py and tests/fake_hermes_gateway.py and pass; the live PONG/engine evidence in the v3.1 ledger was recorded on a machine that had D:/hermes.",
      blocker="Hermes is not installed on this host: no D:/hermes, no HERMES_DIR/HERMES_PYTHON in .env. Live gateway and engine evidence cannot be produced here.",
      next_action="Owner installs Hermes (or authorises FRIDAY to provision it) and then re-run scripts/verify_hermes_engine.py."),
    R("FR-MDL-004", "11", "Local System-1 decision layer (Laya or a benchmarked alternative) for classification, urgency, privacy class, department, intent; benchmarked on target hardware before becoming default; weak decisions escalate instead of fabricating certainty.", P1,
      status=MISSING,
      impl="A deterministic reflex layer and a task classifier exist, but no local model route and no benchmark harness for one.",
      files=["friday/reflex.py", "friday/task_class.py"],
      tests=["tests/test_reflex.py", "tests/test_task_class.py"],
      next_action="Add a local-decision adapter behind the existing classifier seam with a benchmark harness and a confidence floor that escalates.",
      owner_note="Owner accepts Laya/Needle as the lightweight local layer and asked for a researched design."),
    R("FR-MDL-005", "11", "Local tool/structured-extraction layer (Needle 3 or better) for tool selection, typed arguments, extraction and local embedding/search; never bypasses policy; low confidence escalates; tool schemas are discovered selectively.", P1,
      status=PARTIAL,
      impl="Selective capability discovery and lazy skill routing already avoid full-catalog loads; no local model performs the selection.",
      files=["friday/selective.py", "friday/skills/router.py", "friday/capability_router.py"],
      tests=["tests/test_selective.py", "tests/test_skills_golden.py"],
      next_action="Add the local extraction/tool-call adapter with confidence thresholds and a policy check after selection.",
      owner_note="Owner: Needle 3 holds confidential data locally; its data must not reach the cloud model that orchestrates it."),
    R("FR-MDL-006", "11", "Dynamic task-based routing: call the model that fits the task, not a fixed ladder; trivial work never invokes a frontier model; privacy can override cost; user preference can bias or restrict routing.", P0,
      status=PARTIAL,
      impl="Task classification, quota-aware routing ADR, executor router, budget tiers; privacy mode can refuse. No single task-fitness selection with privacy/preference as explicit inputs.",
      files=["friday/task_class.py", "friday/executor_router.py", "friday/model_gateway.py", "friday/routing_memory.py"],
      tests=["tests/test_task_class.py", "tests/test_capability_routing.py", "tests/test_routing_memory.py"],
      next_action="Implement one route-selection function taking task class, privacy class, preference, health, budget and latency, with the decision recorded.",
      owner_note="Owner rejected the reasoning ladder and asked for task-fitness selection instead."),
    R("FR-MDL-007", "11", "Model budget and preferences are part of onboarding and runtime routing; spend/usage tracked where available; exhaustion pauses or escalates rather than silently continuing.", P1,
      status=PARTIAL,
      impl="Token budgets per task class, objective budget, execution economics, governor shedding.",
      files=["friday/objective_budget.py", "friday/execution_economics.py", "friday/model_gateway.py"],
      tests=["tests/test_objective_budget.py", "tests/test_execution_economics.py", "tests/test_task_budget.py"],
      next_action="Ask for budget in onboarding, persist it per workspace, and make the router read it.",
      owner_note="Owner: the budget belongs to the user - ask them."),
    R("FR-MDL-008", "11", "Free Model Scout researches legitimate free tiers, official experimental access and open providers; no leaked keys, quota bypass or deceptive identities; new routes are benchmarked and health-tested; phone/card/identity requirements go to the user.", P1,
      status=MISSING,
      impl="Nothing. Provider health/cooldown and route benchmarking exist and are the seam a scout would feed.",
      files=[],
      tests=[],
      next_action="Implement a scout that records candidate routes with source, terms, auth requirement and a legitimacy flag, requires user action for identity/phone/card, and admits a route only after a health probe.",
      owner_note="Owner wants weekly discovery of free providers and automatic key acquisition where legitimate; explicitly no phone number without permission. Automatic account creation is refused - see PRD-OWN-FREEKEY."),

    # ---------- 12. Secrets and privacy ----------
    R("FR-SEC-001", "12", "API keys, passwords, cookies, payment credentials and tokens are never ordinary model context; negative controls prove raw secrets are absent from cloud requests and normal logs; models see opaque handles; sensitive strings are handled by trusted code.", P0,
      impl="SecretBroker with aliased handles, redaction in audit log, sensitive-domain capture block, vault outside the bundle, doctor prints names only.",
      files=["friday/secret_broker.py", "friday/trust.py", "friday/vault.py", "friday/sensitive_domains.py"],
      tests=["tests/test_secret_broker.py", "tests/test_secret_isolation.py", "tests/test_sensitive_domains.py"],
      next_action="",
      status='VERIFIED',
      evidence="tests/test_secret_broker.py (9, incl. an alias that tries to escape the vault directory) + the redaction path in friday/trust.py + the gateway negative control in tests/test_model_gateway.py that plants a SECRET-shaped value and asserts it is absent from the request that would have been sent. data/prd_baseline (2026-09-23, four chunks, .venv-build, -m 'not live and not slow'): 4,053 passed, 73 skipped, 0 failed, every chunk exit 0"),
    R("FR-SEC-002", "12", "Protected kernel (secret broker, policy engine, verification roots, updater trust roots, permission evaluator, audit integrity) resists self-development; protected changes need explicit approval; some invariants stay non-disableable.", P0,
      impl="Kernel path guard in self_upgrade, constitutional DENY set in user_policy, promotion gate with independent review, selfdev worktree isolation.",
      files=["friday/self_upgrade.py", "friday/user_policy.py", "friday/selfdev.py", "friday/promotion.py"],
      tests=["tests/test_self_upgrade.py", "tests/test_selfdev.py", "tests/test_promotion.py", "tests/test_user_policy.py"],
      next_action="",
      status='VERIFIED',
      evidence='tests/test_invariants.py trust guard: every alias of a kernel path is refused, a symlink into the kernel is refused by its real path, and ordinary paths are not kernel. tests/test_invariants.py 28 passed incl. test_every_invariant_guard_is_load_bearing over 13 guards - each guard is removed in a subprocess and its attack test is required to go RED'),
    R("FR-PRV-001", "12", "Data classified PUBLIC, INTERNAL, PERSONAL, CUSTOMER_PII, CONFIDENTIAL, SECRET, LOCAL_ONLY; LOCAL_ONLY is physically unroutable to disallowed providers; routing is testable without prompts; cloud use requires redaction and refuses when safe reasoning is impossible.", P0,
      impl="friday/data_class.py owns the seven PRD classes, a deterministic classifier over payload text, the class-by-backend rule table (ALLOW / REDACT / REFUSE) and redaction with a count. The model gateway classifies once per request and applies the decision at the single egress seam before a provider call; locality comes from privacy.LOCAL_BACKENDS rather than a second list. Under PRIVATE_ONLY a REDACT-class payload is refused outright, because minimisation is not that mode's promise.",
      files=['friday/data_class.py', 'friday/model_gateway.py', 'friday/privacy.py'],
      tests=['tests/test_data_class.py', 'tests/test_privacy.py', 'tests/test_model_gateway.py'],
      next_action="",
      owner_note="Owner: the cloud model must not receive the actual confidential content yet must still produce the output; refuse and explain when that is impossible.",
      status='VERIFIED',
      evidence="tests/test_data_class.py + test_privacy.py + test_model_gateway.py: 79 passed. Three negative controls: a SECRET-shaped payload is refused and the raw value is absent from the request that would have been sent; a CUSTOMER_PII payload is sent redacted, asserted on the echoed sent payload rather than on the verdict; a SECRET payload still reaches a genuinely local route, which proves the rule is 'unroutable to disallowed cloud providers' and not 'unroutable at all'. The one failure in that run, test_live_hermes_gateway_answers_without_an_agent_loop, is pre-existing and environmental (no Hermes on this host), confirmed by re-running it on a stashed tree."),
    R("FR-PRV-002", "12", "Every user/company/brand workspace is logically and cryptographically separated: a memory query in A cannot return B's data, secrets are namespaced per workspace, browser profiles and private skills are never globally reused without explicit sharing.", P0,
      impl="friday/workspace.py owns tenant identity (ids, current(), use(), guard(), and the SCOPED/UNSCOPED schema declaration). Memory and observation reads and writes are filtered by workspace in friday/store.py, including supersession so one tenant's write cannot retire another's belief; memory_provenance refuses a foreign id rather than returning None. Secrets get a directory and their own Fernet key per workspace with a one-time migration of a pre-boundary install. The private skill tier is per workspace so discovery never crosses. A browser profile approved by one workspace is refused to another.",
      files=['friday/workspace.py', 'friday/store.py', 'friday/secret_broker.py', 'friday/skills/files.py', 'friday/browser.py'],
      tests=['tests/test_workspace_isolation.py'],
      next_action="",
      owner_note="Owner: every user has their own workspace; two FRIDAYs in one company may share only with consent and never secrets.",
      status='VERIFIED',
      evidence="tests/test_workspace_isolation.py 28 passed: all four PRD acceptance criteria covered from the attacker's side (recall, recall_scoped, free-text search, export, provenance-by-id, observations) plus the destructive half (supersede, forget, expire) and the negative control test_an_unfiltered_query_really_would_leak, which reads the table unfiltered and MUST see both tenants so the isolation tests cannot pass on an empty table. Secret key material proven distinct; legacy store adopted not orphaned."),

    # ---------- 13. Memory ----------
    R("FR-MEM-001", "13", "FRIDAY, Hermes, Claude Code, Codex and workers reach durable context only through FRIDAY-controlled memory; workers keep temporary state; a fresh worker continues an objective without the previous chat session; no hidden competing source of truth; access scoped by workspace/user/project/run.", P0,
      impl="Canonical store with memory kinds, scope, provenance, supersede/contradict; memory stack tiers; Hermes memory write-back; objective ledger continuity so a fresh worker resumes.",
      files=["friday/store.py", "friday/memory_stack.py", "friday/continuity.py", "friday/hermes_bridge.py"],
      tests=["tests/test_memory_contract.py", "tests/test_hermes_memory_writeback.py", "tests/test_continuity.py", "tests/test_chaos_restart.py"],
      evidence="tests/test_chaos_restart.py resumes 10/10 objectives across real process kills, so a fresh worker continues from the ledger rather than a chat session; memory access is now workspace-scoped (tests/test_workspace_isolation.py); tests/test_hermes_memory_writeback.py proves Hermes writes back into FRIDAY's store rather than keeping its own. data/prd_baseline (2026-09-23, four chunks, .venv-build, -m 'not live and not slow'): 4,053 passed, 73 skipped, 0 failed, every chunk exit 0",
      next_action="",
      status='VERIFIED'),
    R("FR-MEM-002", "13", "Memory backend abstraction (Mem0 primary candidate, OpenViking for context/resource/skill) so no backend-specific feature becomes the only representation; migration/export possible; memory health observable.", P1,
      status=PARTIAL,
      impl="Fabric adapters for mem0 and graphiti behind the capability fabric; canonical truth stays in the local store; memory export tool exists.",
      files=["friday/fabric_adapters/mem0_memory.py", "friday/fabric_adapters/graphiti_memory.py", "friday/fabric_memory.py", "friday/store.py"],
      tests=["tests/test_fabric.py", "tests/test_memory_contract.py"],
      next_action="Record the division of responsibility as an ADR and add a memory-health section to the doctor."),
    R("FR-MEM-003", "13", "Promotion uses importance, provenance, dedupe, trust, scope and contradiction handling; corrections supersede without deleting history; untrusted web content never becomes durable fact automatically; records expose origin and update time.", P0,
      impl="memory_promotion with candidate/decision, supersedes/contradicts columns, provenance tool, retention and expiry.",
      files=["friday/memory_promotion.py", "friday/store.py"],
      tests=["tests/test_memory_promotion.py", "tests/test_memory_provenance.py"],
      next_action="",
      status='VERIFIED',
      evidence='tests/test_invariants.py memory guard: read material cannot write a rule directly, the promotion gate is the only door and it requires evidence, verified provenance still writes. tests/test_invariants.py 28 passed incl. test_every_invariant_guard_is_load_bearing over 13 guards - each guard is removed in a subprocess and its attack test is required to go RED'),
    R("FR-MEM-004", "13", "Users can inspect, search, correct, delete and understand what FRIDAY remembers; normal users see human concepts, Developer Mode shows provenance/scope/confidence/ids; deleting a memory does not delete unrelated artifacts.", P1,
      status=PARTIAL,
      impl="Memory tools (recall/provenance/export/forget) and a memory graph; UI server exposes skills and activity. No memory inspection UI.",
      files=["friday/toolsets/memory.py", "friday/memory_graph.py", "friday/ui_server.py", "ui/"],
      tests=["tests/test_memory_provenance.py", "tests/test_ui_server.py"],
      next_action="Add a read/delete memory view to the control room over an /api/memory endpoint with provenance in Developer Mode."),
    R("FR-MEM-005", "13", "Research is stored as source-backed artifacts (question, sources, fetched time, facts, synthesis, confidence, freshness, invalidation triggers); fresh prior research is reused; fast-moving facts expire sooner; reuse stays attributable.", P1,
      status=MISSING,
      impl="Research corpus building, dedupe, relevance and grounding exist per call; nothing persists an artifact with freshness or invalidation.",
      files=["friday/toolsets/research.py", "scripts/build_corpus.py"],
      tests=["tests/test_research.py"],
      next_action="Persist research artifacts with freshness classes and reuse them before re-researching.",
      owner_note="Owner: 'fresh enough' means today's or this week's knowledge; save every web search so it is not repeated."),

    # ---------- 14. Skills ----------
    R("FR-SKL-001", "14", "Each workspace may create private skills that teach procedures without granting authority; private skills do not leave the workspace without permission; skills stay inspectable and versioned; users need not learn the file format.", P1,
      impl="The skill layer over Hermes skills (tiers, registry, lint, lifecycle, evaluator, publisher, router, CLI) with the LOCAL_USER tier now bound to a workspace, so discovery in one tenant never reaches another's procedures. Permissions stay informational and a self-verifying skill fails lint.",
      files=["friday/skills/"],
      tests=["tests/test_skills_golden.py", "tests/test_skills_units.py", "tests/test_skills_extensions.py"],
      evidence='docs/product/EXE_AND_SKILL_SYSTEM_LEDGER.md B-1..B-20 with the SK-01..SK-10 journeys; 50 skill tests green; tests/test_workspace_isolation.py::test_private_skill_tiers_are_disjoint_and_shared_tiers_are_not.',
      next_action="",
      status='VERIFIED'),
    R("FR-SKL-002", "14", "Two layers: verified generic global skills and private workspace skills; customer data never promoted into global skills; generic improvements shared only after sanitisation; precedence deterministic.", P1,
      status=VERIFIED,
      impl="Tier roots built-in/user/project/private/public with deterministic precedence; publication lint refuses private data; codebase scopes never public.",
      files=["friday/skills/files.py", "friday/skills/publisher.py", "friday/skills/lint.py", "friday/skills/router.py"],
      tests=["tests/test_skills_golden.py", "tests/test_skills_units.py"],
      evidence="SK-07 conflict precedence and SK-08 scope gate in docs/product/EXE_AND_SKILL_SYSTEM_LEDGER.md; PUB-01 published to a bare origin and re-discovered from a fresh clone."),
    R("FR-SKL-003", "14", "Skill states DISCOVERED, QUARANTINED/DRAFT, LINTED, TESTING, ACTIVE, REVALIDATION, UPDATED, ROLLED_BACK, DEPRECATED, DISABLED; downloaded skills start quarantined, demonstrated skills start draft, only tested skills activate, stale skills cannot stay trusted.", P0,
      status=VERIFIED,
      impl="contracts.TRANSITIONS + lifecycle.py enforce the graph; agent/community sources start QUARANTINED; activation requires a real evaluator report.",
      files=["friday/skills/contracts.py", "friday/skills/lifecycle.py", "friday/skills/evaluator.py"],
      tests=["tests/test_skills_golden.py", "tests/test_skills_units.py"],
      evidence="SK-02/SK-06/SK-10 in the EXE/skill ledger: quarantine on import, rollback of a bad version, forged verification report refused."),
    R("FR-SKL-004", "14", "Repeated verified workflows may become skills; trivial actions do not; low-risk creation may be automatic under policy; the user can see why a skill was created.", P1,
      status=VERIFIED,
      impl="candidates.py applies the ladder criteria and refuses trivial or unverified work; the registry records why.",
      files=["friday/skills/candidates.py", "friday/skill_ladder.py", "friday/skills/registry.py"],
      tests=["tests/test_skills_golden.py", "tests/test_skill_ladder.py"],
      evidence="SK-01 in the EXE/skill ledger: a repeated verified procedure becomes a candidate, a one-off does not."),
    R("FR-SKL-005", "14", "Relevant code/app/workflow changes trigger targeted revalidation; unrelated changes invalidate nothing; dependencies identify affected skills; a failed new version rolls back.", P1,
      status=VERIFIED,
      impl="dependencies.py fingerprints declared paths and git identity; revalidate --since walks a commit range; lifecycle keeps the last verified snapshot.",
      files=["friday/skills/dependencies.py", "friday/skills/lifecycle.py", "friday/skills/change_type.py"],
      tests=["tests/test_skills_golden.py", "tests/test_skills_extensions.py"],
      evidence="SK-03 (dependency change -> NEEDS_REVALIDATION) and SK-04 (README change -> nothing) in the EXE/skill ledger."),
    R("FR-SKL-006", "14", "Effective authority is the intersection of user/workspace policy, objective authority, worker authority and skill manifest; a skill cannot grant itself permissions; prose cannot override a manifest; violations are logged and blocked.", P0,
      status=VERIFIED,
      impl="Skill permissions are informational only; policy_bypass and self_verification are lint FAILs; runtime authority comes from PolicyEngine, which never reads a skill.",
      files=["friday/skills/lint.py", "friday/policy.py", "friday/trust.py"],
      tests=["tests/test_skills_golden.py", "tests/test_policy.py"],
      evidence="Audit rows Q and R in docs/product/EXE_AND_SKILL_SYSTEM_LEDGER.md; SK-10 refuses a self-verifying skill."),

    # ---------- 15. Learning ----------
    R("FR-LRN-001", "15", "Teach Mode: FRIDAY observes an authorised workflow, infers a procedure, asks about decision points, drafts a skill and tests it; no silent capture of unrelated secrets; the user can stop it instantly; the skill passes tests before reuse.", P1,
      status=PARTIAL,
      impl="AutoLearner records verified turns and feeds memory/skill candidates; the skill candidate detector and evaluator cover drafting and testing. There is no explicit observe-a-workflow session with a stop control.",
      files=["friday/autolearn.py", "friday/skills/candidates.py", "friday/skills/evaluator.py"],
      tests=["tests/test_autolearn.py", "tests/test_skills_golden.py"],
      next_action="Add an explicit Teach Mode session (start/stop, capture scope, decision-point questions) on top of the candidate detector."),
    R("FR-LRN-002", "15", "Correction handling: 'no, we never do it like this' stops the path, decides temporary vs durable, and updates the right layer (memory, skill, policy, code, config, test); ambiguity is clarified; the corrected workflow is revalidated.", P1,
      status=PARTIAL,
      impl="Utterance correction records, memory supersede, routing corrections, user policy adaptation in plain conversation, skill revalidation.",
      files=["friday/store.py", "friday/user_policy.py", "friday/routing_memory.py", "friday/first_run.py"],
      tests=["tests/test_routing_memory.py", "tests/test_user_policy.py", "tests/test_memory_promotion.py"],
      next_action="Add one correction router that classifies a correction and dispatches it to the correct layer with an audit record."),
    R("FR-LRN-003", "15", "Bounded deep-learning missions while the user is away, with time, compute, storage, source-quality and stop limits; output has sources, knowledge map, project implications, uncertainties and candidate skills; no accredited-degree claim.", P2,
      status=MISSING,
      impl="Nothing. Objective budgets, the research toolset and the skill candidate pipeline are the pieces a mission would compose.",
      files=[],
      tests=[],
      next_action="Implement a bounded learning mission that produces an expertise pack and candidate skills, with explicit limits and an honest capability claim.",
      owner_note="Owner: 'Hey Jarvis, I'm going to sleep' should start a learning mission on project-relevant topics."),

    # ---------- 16. Dynamic specialist agency ----------
    R("FR-AGT-001", "16", "FRIDAY creates the smallest useful set of workers after learning the user's work, each with a role and expected benefit; count constrained by token/cost/concurrency policy; unused workers sleep.", P1,
      status=PARTIAL,
      impl="Role catalogue, team compilation by goal size, Hermes team submit/poll with idle sweep, governor concurrency cap of 2.",
      files=["friday/roles.py", "friday/hermes_team.py", "friday/governor.py", "friday/org.py"],
      tests=["tests/test_roles.py", "tests/test_hermes_team.py", "tests/test_governor.py"],
      evidence="Governor granted 2 of 6 concurrent requests (v3.1 NFR-P09); team compilation tested.",
      blocker="No live multi-worker run on record (carried from the v3.1 matrix FR-011) and Hermes is absent from this host.",
      next_action="Drive worker creation from the onboarding profile rather than from goal size alone."),
    R("FR-AGT-002", "16", "Workers may have names, roles, personalities, model policies, skills and tools, with function before persona; the user may show or hide detail; Developer Mode exposes full configuration; personality cannot override policy.", P2,
      status=PARTIAL,
      impl="Role dataclass with specialty and tool mapping; org plane assembles a chain. No persona layer and no show/hide control.",
      files=["friday/roles.py", "friday/org.py", "friday/orgplane.py"],
      tests=["tests/test_roles.py", "tests/test_org.py"],
      next_action="Add persona fields to the role record and expose them read-only in the control room.",
      owner_note="Owner wants a proper named team with personas, not generic labels."),
    R("FR-AGT-003", "16", "Worker role and configuration persist while the model session is disposable; a fresh worker reconstructs from ledger, project brain, memory, skills and evidence; long sessions are not the continuity mechanism; a crash does not lose the objective.", P0,
      impl="Objective ledger with checkpoints/leases/wakes, recovery packets, supervisor restart, chaos suite.",
      files=["friday/continuity.py", "friday/continuous.py", "friday/skill_ladder.py", "friday/objectives.py"],
      tests=["tests/test_continuity.py", "tests/test_chaos_restart.py", "tests/test_executor_continuity.py"],
      next_action="",
      status='VERIFIED',
      evidence='tests/test_invariants.py ownership guard (two live owners are impossible, a stale lease recovers, a terminal run cannot be leased) + tests/test_chaos_restart.py 10/10 resume on real processes. tests/test_invariants.py 28 passed incl. test_every_invariant_guard_is_load_bearing over 13 guards - each guard is removed in a subprocess and its attack test is required to go RED'),
    R("FR-AGT-004", "16", "Workers communicate through tasks, evidence, questions, artifacts, decisions and handoffs rather than unbounded chatter; loops are bounded; important decisions persist; the parent gets summaries by default.", P1,
      impl="Handoff records, objective events, bounded agentic loop with fingerprint blocking and strategy-change caps.",
      files=["friday/handoff.py", "friday/continuous.py", "friday/objectives.py"],
      tests=["tests/test_handoff.py", "tests/test_objective_engine.py"],
      next_action="",
      status='VERIFIED',
      evidence="tests/test_handoff.py + tests/test_objective_engine.py: communication is tasks, evidence, questions, artifacts, decisions and handoffs; the loop is bounded by max_attempts, a fingerprint block and a strategy-change cap. data/prd_baseline (2026-09-23, four chunks, .venv-build, -m 'not live and not slow'): 4,053 passed, 73 skipped, 0 failed, every chunk exit 0"),
    R("FR-AGT-005", "16", "Separate FRIDAY installations may communicate when both users permit; shared scope is explicit and logged; secret/private content is filtered by both policies; one FRIDAY cannot silently administer another.", P2,
      status=MISSING,
      impl="Nothing. The companion pairing code is a browser-extension pairing, not FRIDAY-to-FRIDAY federation.",
      files=[],
      tests=[],
      next_action="Design a consent-scoped peer protocol; do not build it before workspace isolation exists.",
      owner_note="Owner: two FRIDAYs in one company may talk with consent, never about secrets."),

    # ---------- 17. Coding workers ----------
    R("FR-COD-001", "17", "Claude Code is a major engineering executor receiving bounded work packets with requirements, constraints, context, tests and completion criteria; FRIDAY verifies major changes independently; trivial edits do not need it.", P1,
      impl="claude_code executor with worktrees and brokers, executor router, development plane, independent review in the promotion gate.",
      files=["friday/executors/claude_code.py", "friday/executors/worktrees.py", "friday/executor_router.py", "friday/development.py"],
      tests=["tests/test_executor.py", "tests/test_executor_router.py", "tests/test_executor_sandbox.py"],
      next_action="",
      status='VERIFIED',
      evidence="tests/test_executor.py 44 + tests/test_executor_router.py 22 + tests/test_executor_sandbox.py: bounded work packets, an isolated worktree per run, and a router that does not send trivial edits to a coding CLI. data/prd_baseline (2026-09-23, four chunks, .venv-build, -m 'not live and not slow'): 4,053 passed, 73 skipped, 0 failed, every chunk exit 0"),
    R("FR-COD-002", "17", "Codex, OpenCode or future coding CLIs can be added through adapters without redesigning orchestration; connected CLIs share authorised project memory/skills; credentials follow official mechanisms.", P2,
      status=PARTIAL,
      impl="Executor broker abstraction and CLI fabric adapters exist; only claude_code is a first-class executor.",
      files=["friday/executors/brokers.py", "friday/fabric_adapters/_cli_adapter.py", "friday/fabric_cli.py"],
      tests=["tests/test_fabric_cli_adapters.py", "tests/test_executor.py"],
      next_action="Add one more CLI through the adapter to prove the seam, sharing the canonical memory.",
      owner_note="Owner: FRIDAY should be able to wire itself to another CLI on request."),
    R("FR-COD-003", "17", "When a CLI asks a multiple-choice question FRIDAY answers automatically when confidence and authority suffice and escalates when the choice is materially uncertain or consequential; the choice and its reasoning are logged.", P1,
      status=MISSING,
      impl="Nothing reads an interactive CLI prompt. The executor runs non-interactive invocations.",
      files=["friday/executors/claude_code.py"],
      tests=[],
      next_action="Implement prompt detection with a confidence-scored answer policy, an escalation path and a Developer Mode log of each decision.",
      owner_note="Owner asked for this explicitly: FRIDAY picks the best option itself and asks only when unsure."),
    R("FR-COD-004", "17", "Structural code memory (codebase-memory-mcp or equivalent) lets coding workers query architecture, call chains, changes and blast radius; the graph is a derived index with observable freshness and no policy authority.", P2,
      status=PARTIAL,
      impl="Local code graph plus a codebase_memory fabric adapter; freshness is not surfaced.",
      files=["friday/codegraph.py", "friday/fabric_adapters/codebase_memory.py"],
      tests=["tests/test_codegraph.py", "tests/test_fabric_code_intel.py"],
      next_action="Expose index freshness and assert the adapter holds no policy authority."),

    # ---------- 18. Capability builder ----------
    R("FR-CAP-001", "18", "When asked for something unsupported FRIDAY says so truthfully and may offer to research and build it; substantial construction asks once unless standing policy allows; the original objective stays linked.", P1,
      status=PARTIAL,
      impl="Capability forge with spec, static gate, sandboxed verification and a registry; honesty layer refuses fabricated capability claims. The ask-once-then-build flow is not wired to an objective.",
      files=["friday/forge.py", "friday/forge_runtime.py", "friday/honesty.py"],
      tests=["tests/test_forge.py", "tests/test_honesty.py"],
      next_action="Wire missing-capability detection to a single approval and keep the originating objective linked through to reuse."),
    R("FR-CAP-002", "18", "Approved construction follows research, docs/GitHub/MCP discovery, compatibility/security/licence review, adopt/adapt/build, quarantine, sandbox, implementation, tests, negative controls, real integration, registration, skill creation; popularity is not trust; provenance recorded; invisible in normal UX, visible in Developer Mode.", P1,
      status=PARTIAL,
      impl="Capability fabric is the single registry with pinned upstreams, licence rules at import, static gate, sandboxed verification, upstream lock and notices.",
      files=["friday/fabric.py", "friday/forge.py", "scripts/upstream_lock.py", "scripts/third_party_notices.py"],
      tests=["tests/test_fabric.py", "tests/test_forge.py", "tests/test_upstream_lock.py"],
      evidence="Fabric Provider.__post_init__ refuses an unpinned upstream and restricts copyleft to isolated modes; the lock is checkable.",
      next_action="Add the research/discovery front end so the pipeline starts from a question rather than from a hand-written spec."),
    R("FR-CAP-003", "18", "Capabilities built for a workspace stay private until sharing is permitted; private code does not enter global packs automatically; sharing needs sanitisation and provenance review; the user can disable or remove a capability.", P1,
      status=PARTIAL, deps=["FR-PRV-002"],
      impl="Forged skills/capabilities are local records; the skill publisher gates public scope behind policy; no workspace ownership on a forged capability.",
      files=["friday/forge.py", "friday/skills/publisher.py"],
      tests=["tests/test_forge.py", "tests/test_skills_golden.py"],
      next_action="Stamp workspace ownership on forged capabilities and refuse cross-workspace reuse."),

    # ---------- 19. Technology scout ----------
    R("FR-UPG-001", "19", "FRIDAY periodically researches models, MCPs, projects, skills, browser frameworks, memory systems and agent infrastructure; research is bounded and source-backed; candidates are compared against existing capability, not adopted for hype; weekly freshness for fast-moving categories.", P2,
      status=MISSING,
      impl="Nothing schedules or records technology research. selfdev/self_upgrade handle promotion once a candidate exists.",
      files=[],
      tests=[],
      next_action="Implement a bounded scout writing research artifacts and candidate comparisons; depends on FR-MEM-005.",
      owner_note="Owner wants weekly discovery so FRIDAY does not fall behind."),
    R("FR-UPG-002", "19", "Material change follows research, sandbox, benchmark, security/compatibility review, proposal, promotion; policy may auto-promote verified low-risk improvements; protected kernel never auto-promotes; every promotion has rollback; regression blocks promotion.", P0,
      impl="selfdev worktree isolation, benchmark gate, promotion decision with independent review, kernel path refusal, rollback.",
      files=["friday/selfdev.py", "friday/selfdev_benchmark.py", "friday/promotion.py", "friday/self_upgrade.py"],
      tests=["tests/test_selfdev.py", "tests/test_promotion.py", "tests/test_self_upgrade.py"],
      next_action="",
      owner_note="Owner allows low-risk auto-promotion.",
      status='VERIFIED',
      evidence="tests/test_selfdev.py + tests/test_promotion.py 34 + tests/test_self_upgrade.py: work happens in an isolated worktree, promotion requires independent review, a kernel path is refused, and a benchmark regression blocks. data/prd_baseline (2026-09-23, four chunks, .venv-build, -m 'not live and not slow'): 4,053 passed, 73 skipped, 0 failed, every chunk exit 0"),

    # ---------- 20. Policy and autonomy ----------
    R("FR-POL-001", "20", "Autonomy slider (Advise, Draft, Ask Before Acting, Autonomous Routine Work, High Autonomy) compiles to granular policy; changes are predictable; high autonomy never disables permanent invariants; Developer Mode can inspect the generated policy.", P1,
      status=PARTIAL,
      impl="Per-domain AUTO/CONFIRM/DENY user policy with constitutional DENY, plus an autonomy field in the first-run contract. No named levels and no compiler.",
      files=["friday/user_policy.py", "friday/first_run.py", "friday/policy.py"],
      tests=["tests/test_user_policy.py", "tests/test_autonomy.py"],
      next_action="Implement the five levels as a deterministic compiler onto the existing domains, refusing to touch the constitutional set.",
      owner_note="Owner wants the slider in the UI."),
    R("FR-POL-002", "20", "Policy is built from the user's actual work rather than a profession template; templates may accelerate but stay editable; policy separates low-risk reversible from consequential; changes have audit history.", P1,
      status=PARTIAL, deps=["FR-ONB-004"],
      impl="Versioned, audited per-domain policy updatable in conversation; risk separation in policy.py categories and reversible.py.",
      files=["friday/user_policy.py", "friday/policy.py", "friday/reversible.py"],
      tests=["tests/test_user_policy.py", "tests/test_policy.py", "tests/test_reversible.py"],
      next_action="Generate the initial policy from the onboarding profile instead of from defaults."),
    R("FR-POL-003", "20", "Payments, refunds, large budget/price changes, account/security changes, permanent deletion and legal commitments get stronger confirmation; the confirmation shows exact target/amount/change; the model cannot forge it; approval binds exact parameters and expiry.", P0,
      impl="Confirmation bound to one exact action with a nonce and expiry, replay protection, spend envelopes, consequential categories in policy.",
      files=["friday/confirmation.py", "friday/policy.py", "friday/user_policy.py", "friday/access.py"],
      tests=["tests/test_confirmation.py", "tests/test_policy.py", "tests/test_replay.py"],
      next_action="",
      owner_note="Owner wants a single detailed confirmation step for refunds and price changes.",
      status='VERIFIED',
      evidence='tests/test_confirmation.py 17 (a yes for one target does not cover another; a confirmation cannot be spent twice; expired cannot be approved or consumed; an unknown nonce is refused) + tests/test_invariants.py authority guard: no form of non-approval becomes authorisation and a timed-out approval leaves zero mutation. tests/test_invariants.py 28 passed incl. test_every_invariant_guard_is_load_bearing over 13 guards - each guard is removed in a subprocess and its attack test is required to go RED'),
    R("FR-POL-004", "20", "'Do not ask me again for this kind of action' stores a scoped authorisation, not global permission; scope covers action/workspace/risk; it is revocable; a materially different action stays unapproved.", P1,
      status=PARTIAL, deps=["FR-PRV-002"],
      impl="Per-domain grants with reasons and spend envelopes carry scope and revocation; scope has no workspace dimension and no risk ceiling.",
      files=["friday/user_policy.py", "friday/confirmation.py"],
      tests=["tests/test_user_policy.py", "tests/test_confirmation.py"],
      next_action="Add workspace and risk bounds to a standing authorisation and prove a materially different action still asks."),
    R("FR-POL-005", "20", "FRIDAY communicates meaningful progress with a user-selectable detail level; progress never fabricates completed work; long tasks expose objective, stage, blockers and next action.", P1,
      impl='friday/progress_digest.py reads a progress_detail preference (detailed / balanced / critical-only) from the first-run contract, defaulting to balanced; the digest changes how much it emits and never what it reports as fact. The preference is the tenth first-run question and is not a permission, so it never touches the audited grant trail.',
      files=['friday/progress_digest.py', 'friday/first_run.py'],
      tests=['tests/test_progress_digest.py', 'tests/test_first_run.py'],
      next_action="",
      owner_note="Owner wants live running commentary, reducible on request - 'tell me the main things only'.",
      status='VERIFIED',
      evidence='80 passed across tests/test_autonomy_slider.py, test_autonomy, test_user_policy, test_first_run and test_progress_digest; balanced output is byte-identical to the previous output, so every pre-existing digest test passes unchanged.'),

    # ---------- 21. Safety gatekeeper ----------
    R("FR-GATE-001", "21", "Critical safety and permission decisions are enforced outside the generative model; a model cannot rewrite the gate that limits it; external webpage instructions cannot override policy; capability execution validates authority at runtime.", P0,
      impl="PolicyEngine per call, trust tiers, kernel path guard, external content treated as data, capability runtime authority check.",
      files=["friday/policy.py", "friday/trust.py", "friday/capability_runtime.py", "friday/self_upgrade.py"],
      tests=["tests/test_policy.py", "tests/test_trust.py", "tests/test_prompt_injection.py", "tests/test_capability_fabric_runtime.py"],
      next_action="",
      status='VERIFIED',
      evidence="tests/test_trust.py 16 (an adversarial prompt cannot change a verdict; injected page content cannot reach destructive tools) + tests/test_action_chain.py 13 (planted instructions authorise nothing; the toolset refuses planted text before resolving anything; removing the gate would be caught) + tests/test_injection_pages.py. data/prd_baseline (2026-09-23, four chunks, .venv-build, -m 'not live and not slow'): 4,053 passed, 73 skipped, 0 failed, every chunk exit 0"),
    R("FR-GATE-002", "21", "Network/security/OSINT tooling sits behind explicit authorisation and scope gates; active actions need authorised target/scope metadata; passive research is distinguished from active testing; workers without security scope cannot reach those surfaces.", P0,
      impl="SecurityAuthorization with target guard, restricted fabric providers requiring authorized_scope, capability router groups.",
      files=["friday/trust.py", "friday/fabric_adapters/security_skills.py", "friday/fabric_adapters/strix_pentest.py", "friday/capability_router.py"],
      tests=["tests/test_trust.py", "tests/test_security_skills.py"],
      next_action="",
      owner_note="Owner wants a gatekeeper that prevents any model from misusing FRIDAY's capabilities for attacks.",
      status='VERIFIED',
      evidence="tests/test_trust.py: contract expiry and unbounded scope are refused, and the fabric refuses a security call outside its authorised scope and audits the refusal. data/prd_baseline (2026-09-23, four chunks, .venv-build, -m 'not live and not slow'): 4,053 passed, 73 skipped, 0 failed, every chunk exit 0"),

    # ---------- 22. Business capability ----------
    R("FR-CS-001", "22", "Customer support can draft, reply, analyse and escalate per workspace policy; replies use approved brand/product/policy context; compensation/refund decisions follow authority policy; customer PII is minimised and not promoted to unrelated memory.", P1,
      status=PARTIAL, deps=["FR-PRV-001", "FR-PRV-002"],
      impl="Communication toolsets, customer_email/customer_service policy domains, company playbook fabric pack, memory retention classes.",
      files=["friday/toolsets/", "friday/user_policy.py", "friday/fabric_adapters/company_playbooks.py"],
      tests=["tests/test_fabric_commerce_and_packs.py", "tests/test_user_policy.py"],
      next_action="Add a PII minimisation rule on the support path once data classification exists."),
    R("FR-COMP-001", "22", "Competitor intelligence over public products, prices, offers, reviews, SEO pages and public social content using browser/extraction tooling; artifacts preserve source and freshness; monitoring frequency bounded; access restrictions respected.", P2,
      status=PARTIAL, deps=["FR-MEM-005"],
      impl="Scrapling parse, maxun scraping and agent-reach adapters, research toolset, schedules with bounded frequency, netguard.",
      files=["friday/fabric_adapters/scrapling_parse.py", "friday/fabric_adapters/maxun_scraping.py", "friday/toolsets/research.py", "friday/netguard.py"],
      tests=["tests/test_fabric_scrapling.py", "tests/test_research.py", "tests/test_schedules.py"],
      next_action="Persist competitor findings as research artifacts with freshness."),
    R("FR-WEB-001", "22", "FRIDAY may modify Shopify themes, Next.js storefronts and related code through approved engineering workers with browser QA; changes are tested before release where feasible; deploy/publish follows policy; browser verification confirms the visible result.", P2,
      status=PARTIAL,
      impl="Coding executors with worktrees, deploy_website policy domain, browser verification primitives. No storefront-specific worker.",
      files=["friday/executors/", "friday/user_policy.py", "friday/browser.py"],
      tests=["tests/test_executor.py", "tests/test_browser_primitives.py"],
      next_action="Compose a storefront worker from the existing executor and browser-verify seams."),
    R("FR-CRT-001", "22", "Creative software control (DaVinci Resolve, Adobe and similar) via scripting/MCP/UI or newly built adapters; missing capability invokes the Capability Builder rather than fabricating success; exports verified; media operations respect budgets.", P2,
      status=MISSING, deps=["FR-CAP-001", "FR-CAP-002"],
      impl="Nothing for creative applications. Desktop control, the forge and the honesty gate are the pieces.",
      files=["friday/toolsets/desktop.py", "friday/forge.py"],
      tests=[],
      next_action="Use this as the Capability Builder acceptance case required by the release gate.",
      owner_note="Owner names DaVinci explicitly as the benchmark capability build."),

    # ---------- 23. Voice, telephony, mobile ----------
    R("FR-VOI-001", "23", "Natural multilingual conversation with dynamic language handling and a distinct canonical FRIDAY voice; latency measured not assumed; customer-facing voice may differ from the private one.", P1,
      status=PARTIAL,
      impl="LiveKit voice pipeline (Sarvam STT, Gemini LLM, OpenAI TTS), TTS text cleaning and rate, turn timing instrumentation.",
      files=["agent_friday.py", "friday/voice_brain.py", "friday/turn_timing.py", "friday/response_render.py"],
      tests=["tests/test_voice*.py", "tests/test_response_render.py", "tests/test_turn_timing.py"],
      blocker="LiveKit credentials in .env are the .env.example placeholders; LiveKit answers 401, so no live voice latency can be measured here (EXE ledger A-16).",
      next_action="Owner supplies LiveKit credentials; then measure the voice latency chain.",
      owner_note="Owner: one multilingual voice; FRIDAY's own voice is distinct and not reused for customer care."),
    R("FR-VOI-002", "23", "Barge-in, optional push-to-talk, optional local wake word, microphone mute and echo suppression; mute stops audio reaching STT; FRIDAY does not answer its own TTS; interruption can cancel or redirect the objective.", P1,
      impl="Interruption arbiter, voice input mute gate, echo/self-response guard, cancellation path.",
      files=["friday/arbiter.py", "friday/voice_input.py", "agent_friday.py"],
      tests=["tests/test_arbiter.py", "tests/test_voice_interruption.py", "tests/test_voice_input.py"],
      evidence="tests/test_arbiter.py 11 + tests/test_voice_interruption.py 9 + tests/test_invariants.py cancellation guard (nothing starts after cancel is accepted), proven load-bearing. The mic-race fix is commit 030795f. data/prd_baseline (2026-09-23, four chunks, .venv-build, -m 'not live and not slow'): 4,053 passed, 73 skipped, 0 failed, every chunk exit 0",
      next_action="",
      status='VERIFIED'),
    R("FR-PHN-001", "23", "Telephony behind a policy-governed layer: receive/make calls, book appointments, talk to customers, escalate, summarise; recording follows workspace/legal policy; results structured and auditable; high-risk commitments governed by the same policy engine.", P2,
      status=MISSING,
      impl="Nothing. LiveKit is present for voice but no SIP/telephony adapter, no call policy, no call record.",
      files=[],
      tests=[],
      next_action="Design the adapter boundary first; do not hardcode a carrier.",
      owner_note="Owner accepts telephony, call recording and escalation. Customer-facing AI disclosure is a legal decision - see PRD-OWN-DISCLOSE."),
    R("FR-MOB-001", "23", "Android automation via Google Artemis or a reviewed equivalent through MCP/SDK; phone capabilities permissioned individually; the adapter is security-reviewed before activation; iOS is a separate future path.", P3,
      status=MISSING,
      impl="Nothing for Android. friday/companion/ is a Chrome-extension pairing bridge, not a mobile adapter.",
      files=[],
      tests=[],
      next_action="Audit github.com/google/artemis before any adoption; keep it an adapter with per-capability permissions.",
      owner_note="Owner supplied the Artemis link and calls mobile a later task."),

    # ---------- 24. Remote and 24/7 ----------
    R("FR-REM-001", "24", "Remote surfaces in order Telegram, secure web, mobile app; work executes on the host; remote sender/device strongly authenticated; start/stop/pause and approvals remotely; replay protection and audit logs.", P1,
      status=PARTIAL,
      impl="Authenticated remote objective over the UI server with a face/PIN session gate, source_channel tagging, nonce replay protection, audit log. No Telegram channel.",
      files=["friday/ui_server.py", "friday/access.py", "friday/objectives.py"],
      tests=["tests/test_remote_channel.py", "tests/test_replay.py", "tests/test_access.py"],
      next_action="Add a Telegram channel with pairing, per-sender identity and the same policy path; never trust a display name.",
      owner_note="Owner: Telegram first, then web and mobile; remote authority may differ from local."),
    R("FR-RT-001", "24", "Always-available background runtime rather than a separate server mode; idle never invokes expensive models continuously; auto-start after reboot when configured; objectives and checkpoints survive restart; always-on desktop use supported.", P1,
      status=PARTIAL,
      impl="Launcher supervisor runs roles and restarts them; objective checkpoints/leases survive restart; power state and keep-awake handling; soak harness. No auto-start registration.",
      files=["friday/launcher.py", "friday/continuous.py", "friday/power_state.py", "scripts/soak.py"],
      tests=["tests/test_launcher.py", "tests/test_chaos_restart.py", "tests/test_soak.py", "tests/test_power.py"],
      next_action="Add opt-in auto-start (per-user run entry or scheduled task) plus a startup status check, and measure idle cost.",
      owner_note="Owner: 24/7 is the normal mode; FRIDAY restarts with the laptop and checks status itself."),
    R("FR-PRO-001", "24", "Proactive detection of unanswered customers, 404s, unexpected prices, inventory anomalies, delayed high-value orders, ad-spend anomalies, ranking drops and failed automations; low-risk reversible actions may auto-execute; consequential anomalies alert and hold; non-urgent ideas collect in an Opportunities Inbox.", P1,
      status=PARTIAL,
      impl="Scheduler with runs, world monitor, health checks, automations with executions. No anomaly detectors and no opportunities inbox.",
      files=["friday/toolsets/schedules.py", "friday/world_monitor.py", "friday/health.py", "friday/toolsets/automations.py"],
      tests=["tests/test_schedules.py", "tests/test_world_monitor.py", "tests/test_automations.py"],
      next_action="Add an event-driven anomaly path with alert-and-hold default and an Opportunities Inbox store.",
      owner_note="Owner: alert and hold first; act when approved or when the action is reversible under policy."),

    # ---------- 25. Action truth ----------
    R("FR-TRU-001", "25", "One normalised action-evidence ledger across FRIDAY, MCP, Hermes, Claude Code, objective workers, browser, desktop and scheduler; evidence correlates turn, objective, action, executor, capability, operation, target, status and verification; an unrelated success cannot support a claim; historical claims cite historical evidence.", P0,
      impl="The unified evidence ledger plus, new on this tree, target correlation in friday/honesty.py: a claim naming a concrete target (a filename, a path, a URL, a quoted string) is refused unless a succeeded action recorded that same target. Before this a verified write to foo.txt satisfied every check for the sentence 'I created bar.txt' - right verb, right tool family, real verification, wrong file.",
      files=['friday/honesty.py', 'friday/store.py', 'friday/objectives.py'],
      tests=['tests/test_action_truth_targets.py', 'tests/test_action_chain.py', 'tests/test_invariants.py'],
      evidence="tests/test_action_truth_targets.py 20 passed: the PRD's own example goes RED, the honest claim about the same file stays GREEN, the same file named three ways is recognised, a claim with no concrete target is left to the kind check, a URL claim is checked against the URL touched, and test_removing_the_target_check_would_be_caught restores the pre-guard behaviour in-process and requires the false claim to pass - so the passing test is detecting the guard. Plus tests/test_invariants.py::test_audit_a_completion_claim_the_run_does_not_back_is_refused.",
      next_action="",
      status='VERIFIED'),
    R("FR-TRU-002", "25", "Claims are classified success, failure, attempt, state, perception, completion; 'I tried' needs attempt evidence, 'I couldn't' needs failure evidence, 'I completed' needs verifier evidence; state claims need fresh authoritative state or qualification.", P0,
      impl="Claim finder and required-evidence mapping in honesty.py with a safe alternative, completion gate in store/evaluation.",
      files=["friday/honesty.py", "friday/evaluation.py", "friday/store.py"],
      tests=["tests/test_honesty.py", "tests/test_evaluation.py", "tests/test_completion_gate.py"],
      next_action="",
      status='VERIFIED',
      evidence='tests/test_invariants.py completion guard (a run cannot be completed without passing evidence; other terminal states need none) and audit guard (a completion claim the run does not back is refused, and the safe alternative describes the run as it is), both proven load-bearing. tests/test_invariants.py 28 passed incl. test_every_invariant_guard_is_load_bearing over 13 guards - each guard is removed in a subprocess and its attack test is required to go RED'),
    R("FR-TRU-003", "25", "Claims are audited before user-visible output reaches TTS, transcript and UI; a rejected claim cannot leak through another path; corrections are re-audited; the logic has negative controls.", P0,
      impl="Fail-closed reply audit on the LiveKit path with the same audit on every output fan-out.",
      files=["agent_friday.py", "friday/honesty.py", "friday/response_render.py"],
      tests=["tests/test_honesty.py", "tests/test_reply_audit.py"],
      next_action="",
      status='VERIFIED',
      evidence="tests/test_voice_brain_ui.py reply-audit class: a claim with no tool call at all is replaced, the escalated second claim is replaced too, a read-only family cannot back an action claim, and a claim backed by a family that acts is left alone - the last being the green half that stops the guard passing by refusing everything. data/prd_baseline (2026-09-23, four chunks, .venv-build, -m 'not live and not slow'): 4,053 passed, 73 skipped, 0 failed, every chunk exit 0"),
    R("FR-TRU-004", "25", "Exact user literals (paths, quoted content, URLs, emails, phone numbers, times, branches, versions, ids) are immutable constraints; the planner may change HOW but not WHAT; corruption tests cover files, messages, calendar, branches and URLs; simple requests skip reinterpretation.", P0,
      impl='Literal preservation in the planner and request shaping, a direct-action path for simple bounded requests, and new coverage at the claim layer: friday/honesty.named_targets extracts the literal a claim names and the target check compares it without rewriting it.',
      files=["friday/planner.py", "friday/request_shape.py", "friday/semantics.py"],
      tests=['tests/test_action_truth_targets.py', 'tests/test_capture.py', 'tests/test_request_shape.py', 'tests/test_planner.py'],
      next_action='Add end-to-end corruption tests through the planner for the two families the PRD names that are not covered at that layer: calendar times and message bodies.',
      status='PARTIAL',
      evidence='tests/test_action_truth_targets.py covers eight literal families at the claim layer (paths, relative paths, URLs, email, phone, branch, SKU, order id) and asserts the input string is not mutated; tests/test_capture.py keeps the new value and not both on a from/to correction.'),
    R("FR-UNDO-001", "25", "Reversible actions record before state, after state, reversal procedure, evidence, objective and safety conditions; undo never invents previous state; unsafe or stale reversals are refused or confirmed; the user can undo an action or group.", P1,
      impl="reversible.py records an Attempt with restore, refuses a stale or unsafe restore; files go to the recycle bin rather than rm.",
      files=["friday/reversible.py", "friday/toolsets/files.py"],
      tests=["tests/test_reversible.py", "tests/test_files_recycle.py"],
      next_action="",
      status='VERIFIED',
      evidence="tests/test_reversible.py 17 (a stale or unsafe restore is refused; undo never invents a previous state) + tests/test_files_recycle.py (deletes go to the recycle bin, never rm). data/prd_baseline (2026-09-23, four chunks, .venv-build, -m 'not live and not slow'): 4,053 passed, 73 skipped, 0 failed, every chunk exit 0"),

    # ---------- 26. Packaging, self-repair, recovery ----------
    R("FR-PKG-001", "26", "Normal distribution is an installer/EXE, not a source checkout; no Python/Node/Git knowledge needed; Developer Mode/source workflow stays available; bundled application and mutable user state are separated.", P1,
      status=VERIFIED,
      impl="PyInstaller onedir bundle plus a distribution zip, launcher entry point, STATE_ROOT outside the bundle with legacy migration, source workflow unchanged.",
      files=["scripts/build_windows.py", "friday/launcher.py", "friday/config.py", "friday_exe.py", "packaging/"],
      tests=["tests/test_launcher.py", "tests/test_state_root.py", "tests/test_build_identity.py"],
      evidence="docs/build/FRIDAY_EXE_VALIDATION.md: build 5 golden journeys 15/15 (EXE-00..13) and clean-machine 6/6 with PATH stripped of Python/uv/git and a fresh profile; state survived a rebuild."),
    R("FR-PKG-002", "26", "FRIDAY diagnoses and repairs broken runtime components where safe; optional integration failure never crashes core; repair actions are evidence-backed; destructive repair needs stronger policy or a backup.", P1,
      status=PARTIAL,
      impl="Doctor with per-section verdicts, supervisor restart, circuit breaker, fabric providers report UNAVAILABLE rather than raising, resilience helpers. Repair is largely manual.",
      files=["friday/launcher.py", "friday/resilience.py", "friday/breaker.py", "friday/fabric.py"],
      tests=["tests/test_launcher_doctor.py", "tests/test_resilience.py", "tests/test_breaker.py", "tests/test_fabric.py"],
      next_action="Add a repair action set keyed to doctor findings, each recording cause, repair and evidence."),
    R("FR-PKG-003", "26", "When an update needs a reboot FRIDAY persists state, confirms where appropriate, restarts, auto-starts, rechecks and resumes; no duplicate external side effect; post-restart health checked; failed recovery gives an actionable status.", P1,
      status=PARTIAL, deps=["FR-RT-001"],
      impl="Objective checkpoints and leases survive restart, idempotent external action keys, chaos suite covers process death. Reboot-specific resume and auto-start are missing.",
      files=["friday/continuous.py", "friday/continuity.py", "friday/store.py"],
      tests=["tests/test_chaos_restart.py", "tests/test_continuity.py"],
      next_action="Add a reboot marker plus an on-start resume check, and prove no duplicate side effect across a real restart."),
    R("FR-BKP-001", "26", "FRIDAY can back up memory, skills, policy, project brain and configuration while keeping secrets protected; restore can move the brain to another supported machine; secrets are never exported in plaintext; backup failure is visible.", P1,
      status=MISSING,
      impl="Nothing. friday/vault.py is a document vault, not a backup/restore path; memory export covers memories only.",
      files=["friday/vault.py", "friday/store.py"],
      tests=[],
      next_action="Implement export/import of the brain (memory, skills, policy, profile) with secrets excluded by construction and a visible failure."),

    # ---------- 27-30. Cross-cutting product surfaces ----------
    R("PRD-DEV-001", "27", "Developer Mode exposes raw logs, provider/model routes, token/cost, MCP servers, capabilities, skills and state, memory provenance, workers, ledgers, action evidence, policy decisions, browser traces, database health, CI/test state, plugins and scout findings; Normal Mode shows Memory, Workers, Capabilities, Models, Permissions, Activity, Objectives.", P1,
      status=PARTIAL,
      impl="Control room UI server with activity and a read-only skills API, observability records, audit log, doctor. No mode switch and no unified developer surface.",
      files=["friday/ui_server.py", "ui/", "friday/observability.py", "friday/trust.py"],
      tests=["tests/test_ui_server.py", "tests/test_ui_server_skills.py", "tests/test_observability.py"],
      next_action="Add the Normal/Developer mode switch and fill the developer panels from the stores that already hold the data.",
      owner_note="Owner calls Developer Mode the main mode and wants full detail there."),
    R("PRD-SPAR-001", "28", "Important decisions may use a bounded specialist panel (Evidence Researcher, Optimistic Analyst, Contrarian, Failure Analyst, Domain Specialist, Judge); simple tasks do not pay that cost.", P2,
      status=VERIFIED,
      impl="adversarial.py runs the five-role panel with a judge and is invoked for consequential decisions only.",
      files=["friday/adversarial.py", "friday/promotion.py"],
      tests=["tests/test_adversarial.py"],
      evidence="v3.1 matrix FR-008: 14 adversarial tests green including a live DEFER at 40; promotion requires independent review.",
      owner_note="Owner wants a truthful sparring partner, not a yes-man."),
    R("PRD-EXT-001", "29", "Every third-party system is an internal building block; branding may be hidden in normal UX but internal provenance, licence obligations, source/version and security status stay auditable.", P0,
      impl="The capability fabric is the single registry; Provider.__post_init__ refuses an unpinned upstream and restricts copyleft to isolated modes; upstream lock and generated notices.",
      files=["friday/fabric.py", "scripts/upstream_lock.py", "scripts/third_party_notices.py", "third_party/UPSTREAM_LOCK.json", "docs/integrations/"],
      tests=["tests/test_fabric.py", "tests/test_upstream_lock.py", "tests/test_third_party_notices.py"],
      evidence="tests/test_fabric.py + tests/test_upstream_lock.py + tests/test_third_party_notices.py: an unpinned upstream is refused at import, copyleft is restricted to isolated modes, and scripts/upstream_lock.py --check detects drift. data/prd_baseline (2026-09-23, four chunks, .venv-build, -m 'not live and not slow'): 4,053 passed, 73 skipped, 0 failed, every chunk exit 0",
      owner_note="Owner wants third-party code invisible in the product. Reconciled: invisible in UX, auditable internally - the PRD requires provenance and the licence obligations are real. See PRD-OWN-LICENCE.",
      status='VERIFIED'),
    R("PRD-OSINT-001", "30", "The security/OSINT tool pack is not globally available; active use needs authorised target/scope metadata; passive public research is separated from active testing.", P0,
      impl="Restricted fabric providers demand authorized_scope; SecurityAuthorization target guard; router groups keep the surface off by default.",
      files=["friday/trust.py", "friday/fabric_adapters/security_skills.py", "friday/capability_router.py"],
      tests=["tests/test_trust.py", "tests/test_security_skills.py"],
      next_action="",
      status='VERIFIED',
      evidence="Carried by FR-GATE-002's evidence (the scope gate and the audited refusal)."),

    # ---------- 31. Non-functional ----------
    R("NFR-PERF-001", "31.1", "Local UI response under 200 ms for simple interactions; command acknowledgement under 500 ms; voice acknowledgement under 700 ms median; cancellation under 300 ms; local memory query P95 under 500 ms.", P1,
      status=PARTIAL,
      impl="Performance profile harness measures memory P95 and several targets; voice latencies need live LiveKit.",
      files=["scripts/perf_profile.py", "scripts/measure.py", "friday/runtime_metrics.py"],
      tests=["tests/test_perf.py", "tests/test_turn_timing.py"],
      evidence="v3.1 matrix: memory query P95 89 ms; NFR-P06..P13 measured and passing; P01..P05 NOT_MEASURED.",
      blocker="Voice acknowledgement cannot be measured without LiveKit credentials.",
      next_action="Re-measure on this tree and record which targets stay unmeasured and why."),
    R("NFR-PERF-002", "31.1", "Idle avoids continuous frontier inference and expensive delegation is exceptional for trivial tasks.", P1,
      status=PARTIAL,
      impl="Deterministic-first routing, reflex layer, governor, task classification; the golden suite runs with zero model calls.",
      files=["friday/reflex.py", "friday/task_class.py", "friday/governor.py"],
      tests=["tests/test_reflex.py", "tests/test_task_class.py"],
      next_action="Measure idle model calls and RSS over a soak window and record the number."),
    R("NFR-REL-001", "31.2", "Objective persistence, crash recovery, bounded retries, idempotent external actions, database durability, evidence-based capability health, no false claims, safe restart/resume, degraded plugins do not kill core.", P0,
      impl="All nine mechanisms exist (objective ledger, chaos-tested recovery, retry caps, idempotency keys, WAL/busy timeout, evidence-based health, honesty gate, supervisor, fabric UNAVAILABLE).",
      files=["friday/continuous.py", "friday/store.py", "friday/dbconn.py", "friday/fabric.py", "friday/honesty.py"],
      tests=["tests/test_chaos_restart.py", "tests/test_dbconn.py", "tests/test_fabric.py", "tests/test_honesty.py"],
      next_action="",
      status='VERIFIED',
      evidence='All nine mechanisms have evidence on this tree: objective persistence and safe restart (tests/test_chaos_restart.py 10/10 on real processes), bounded retries and the failure-loop guard (tests/test_objective_engine.py), idempotent external actions (invariants scheduler guard: one execution key has one side effect across a crash, and the claim is a primary key so two processes cannot both win), database durability (invariants persistence guard: a transaction killed midway is all or nothing, integrity_check ok), evidence-based capability health (invariants health guards), no false claims (FR-TRU-001..003), degraded plugins (invariants kernel guard: an adapter that explodes at import cannot take the registry down). tests/test_invariants.py 28 passed incl. test_every_invariant_guard_is_load_bearing over 13 guards - each guard is removed in a subprocess and its attack test is required to go RED'),
    R("NFR-SEC-001", "31.3", "Least privilege, deterministic policy, protected trust roots, prompt-injection boundary, replay protection, secret redaction/isolation, capability permission manifests, signed/validated update path, audit trail, active-security authorisation.", P0,
      status=PARTIAL, deps=["FR-SEC-001", "FR-SEC-002", "FR-GATE-001"],
      impl="Nine of ten exist and are tested; code signing is NOT_CONFIGURED (no certificate on this host).",
      files=["friday/policy.py", "friday/trust.py", "friday/access.py", "friday/secret_broker.py", "scripts/build_windows.py"],
      tests=["tests/test_policy.py", "tests/test_trust.py", "tests/test_replay.py", "tests/test_prompt_injection.py"],
      blocker="No code-signing certificate on this host; the build records signing_status NOT_CONFIGURED.",
      next_action="Re-verify the nine; keep signing BLOCKED_EXTERNAL until the owner supplies a certificate."),
    R("NFR-A11Y-001", "31.4", "Text alternative to voice, keyboard-operable controls, visible focus, captions/transcripts where practical, status not by colour alone, reduced-motion option for the HUD.", P2,
      status=NOT_AUDITED,
      impl="The control room has a text path and a transcript; accessibility has not been audited.",
      files=["ui/", "friday/ui_server.py"],
      tests=["tests/test_ui_server.py"],
      next_action="Audit the control room against the six points and fix what fails."),
    R("NFR-MAINT-001", "31.5", "Adapter boundaries around providers/integrations, no monolithic god module where avoidable, versioned data migrations, testable architectural invariants, optional integrations fail gracefully, internal provenance for external components.", P1,
      status=PARTIAL,
      impl="Fabric adapter boundary, store migrations, layering ADR with a reachability ratchet, fabric UNAVAILABLE on absence, upstream lock. model_gateway.py at 1,201 lines and store.py at 2,874 are the god-module risk.",
      files=["friday/fabric.py", "friday/store.py", "friday/reachability.py", "docs/adr/ADR-001-layering-rule.md"],
      tests=["tests/test_reachability.py", "tests/test_fabric.py", "tests/test_dbconn.py"],
      next_action="Keep the ratchet green; split a god module only with a test-covered seam."),

    # ---------- 32. Journeys ----------
    R("PRD-JRN-A", "32", "Journey A, new ecommerce brand: install, provision, interview, identify platforms, detect Chrome and software, sign in through normal flows, connect model accounts, import approved docs, propose a minimal team, accept/reject workers, personalised benchmark reporting real readiness.", P1,
      status=MISSING, deps=["FR-ONB-001", "FR-ONB-004", "FR-AGT-001"],
      impl="Individual pieces exist; the journey does not run end to end.",
      files=[], tests=[],
      next_action="Build the journey once onboarding and workspace identity exist."),
    R("PRD-JRN-B", "32", "Journey B, update a product: exact literals preserved, policy evaluates risk, browser opens the approved profile, change performed, state read back, evidence recorded, truthful report.", P1,
      status=PARTIAL, deps=["FR-BRW-004", "FR-TRU-001", "FR-TRU-004"],
      impl="Every step has an implementation and a test; no single golden journey ties them together against a real storefront.",
      files=["friday/browser.py", "friday/planner.py", "friday/store.py"],
      tests=["tests/test_cross_capability_journey.py", "tests/test_action_chain.py"],
      next_action="Add the journey against a controlled test page, then a real storefront when credentials exist."),
    R("PRD-JRN-C", "32", "Journey C, missing DaVinci capability: unavailable, ask once, research, review/sandbox, build, tests and negative controls, register, private skill, complete the original task.", P2,
      status=MISSING, deps=["FR-CRT-001", "FR-CAP-001", "FR-CAP-002"],
      impl="The forge covers build/verify/register; nothing covers discovery and the original-task link.",
      files=["friday/forge.py"], tests=["tests/test_forge.py"],
      next_action="This is the release gate's Capability Builder acceptance case."),
    R("PRD-JRN-D", "32", "Journey D, repeated workflow becomes a skill: detector, draft, lint/security/procedure/trigger tests, ACTIVE, later revalidation.", P1,
      impl="candidates -> curator -> lint -> evaluator -> lifecycle -> dependencies, end to end in the skill layer.",
      files=["friday/skills/"],
      tests=["tests/test_skills_golden.py"],
      evidence="SK-01..SK-06 in docs/product/EXE_AND_SKILL_SYSTEM_LEDGER.md run detector to draft to lint to evaluator to ACTIVE to revalidation-after-a-dependency-change, and 50 skill tests are green in data/prd_baseline (2026-09-23, four chunks, .venv-build, -m 'not live and not slow'): 4,053 passed, 73 skipped, 0 failed, every chunk exit 0",
      status='VERIFIED'),
    R("PRD-JRN-E", "32", "Journey E, Telegram remote command: authenticated message creates an objective, work runs on the host, progress sent remotely, consequential approval requested remotely, verified result returned.", P1,
      status=PARTIAL, deps=["FR-REM-001"],
      impl="The same journey works over the authenticated web channel; Telegram is absent.",
      files=["friday/ui_server.py", "friday/objectives.py"],
      tests=["tests/test_remote_channel.py"],
      next_action="Repeat the journey over Telegram once the channel exists."),
    R("PRD-JRN-F", "32", "Journey F, deep learning mission: bounded mission, high-quality sources, structured source-backed knowledge, project implications and candidate skills, morning report with remaining uncertainty.", P2,
      status=MISSING, deps=["FR-LRN-003", "FR-MEM-005"],
      impl="Nothing.", files=[], tests=[],
      next_action="Build after the research artifact cache."),
    R("PRD-JRN-G", "32", "Journey G, error detection: a 10x price anomaly is detected, policy decides alert/hold or reversible correction, the user sees old and new value with evidence, approved correction executes and verifies.", P1,
      status=MISSING, deps=["FR-PRO-001"],
      impl="Nothing detects the anomaly; confirmation and reversible action exist for the response.",
      files=["friday/confirmation.py", "friday/reversible.py"], tests=[],
      next_action="Build with the proactive anomaly path."),

    # ---------- 33. Success metrics (trust targets are testable invariants) ----------
    R("PRD-MET-TRUST", "33.5", "Zero unauthorised high-risk actions, zero secrets leaked through normal prompt paths, zero cross-workspace memory leakage.", P0,
      impl="The first two have gates and tests; the third has no implementation because workspaces do not exist yet.",
      files=["friday/policy.py", "friday/secret_broker.py"],
      tests=["tests/test_policy.py", "tests/test_secret_broker.py"],
      evidence='All three legs now have tests on this tree: zero unauthorised high-risk actions (tests/test_invariants.py 28 passed incl. test_every_invariant_guard_is_load_bearing over 13 guards - each guard is removed in a subprocess and its attack test is required to go RED, authority/trust/cancellation guards); zero secrets in normal prompt paths (tests/test_model_gateway.py negative controls assert the raw value is absent from what would have been sent); zero cross-workspace leakage (tests/test_workspace_isolation.py).',
      next_action="",
      status='VERIFIED'),
    R("PRD-MET-001", "33", "Utility, reliability, error-reduction, intelligence-efficiency and adaptation metrics are collected so the product can be judged on outcomes.", P2,
      status=PARTIAL,
      impl="Golden suite scores completion, false claims and clarifications; runtime metrics and gateway telemetry record cost/tokens; skill registry records usage and quality.",
      files=["friday/golden.py", "friday/runtime_metrics.py", "friday/model_gateway.py", "friday/skills/registry.py"],
      tests=["tests/test_golden.py", "tests/test_runtime_metrics.py"],
      next_action="Aggregate the existing counters into one metrics report rather than adding new collection."),

    # ---------- 34. Release acceptance gate (the 29 checkboxes) ----------
    R("PRD-GATE-001", "34", "Clean Windows installer provisions the full runtime.", P1, deps=["FR-ONB-001"], status=PARTIAL,
      evidence="Clean-machine validation passes for FRIDAY itself (6/6, build 5).",
      next_action="Extend to Hermes, browser runtime and coding CLI provisioning."),
    R("PRD-GATE-002", "34", "8 GB / 4-core / no-GPU baseline remains usable.", P1, deps=["FR-ONB-003"], status=PARTIAL,
      next_action="Measure on a constrained profile and record idle cost."),
    R("PRD-GATE-003", "34", "Progressive onboarding creates user/workspace profile and policy.", P1, deps=["FR-ONB-004", "FR-PRV-002"], status=MISSING),
    R("PRD-GATE-004", "34", "Official model connections work where supported.", P1, deps=["FR-MDL-002"], status=BLOCKED_EXTERNAL,
      blocker="No provider credentials configured on this host beyond placeholders; Hermes absent."),
    R("PRD-GATE-005", "34", "A local intelligence route is available.", P1, deps=["FR-MDL-004", "FR-MDL-005"], status=MISSING),
    R("PRD-GATE-006", "34", "Secret Broker negative-control tests pass.", P0, deps=["FR-SEC-001"],
      status='VERIFIED',
      evidence="Carried by FR-SEC-001's evidence (the broker's own negative controls)."),
    R("PRD-GATE-007", "34", "Raw secrets absent from cloud-model prompt and log tests.", P0, deps=["FR-SEC-001"],
      status='VERIFIED',
      evidence='Carried by FR-SEC-001 and FR-PRV-001: the gateway negative control asserts the raw value is absent from the request that would have been sent.'),
    R("PRD-GATE-008", "34", "Workspace isolation passes adversarial tests.", P0, deps=["FR-PRV-002"],
      status='VERIFIED',
      evidence='tests/test_workspace_isolation.py 28 passed, adversarial, with a negative control that proves the guard can go red.'),
    R("PRD-GATE-009", "34", "Canonical memory works across worker/session replacement.", P0, deps=["FR-MEM-001"],
      status='VERIFIED',
      evidence="Carried by FR-MEM-001's evidence."),
    R("PRD-GATE-010", "34", "Memory UI inspect/edit/delete works.", P1, deps=["FR-MEM-004"], status=MISSING),
    R("PRD-GATE-011", "34", "Research provenance and freshness work.", P1, deps=["FR-MEM-005"], status=MISSING),
    R("PRD-GATE-012", "34", "Dynamic team creation works.", P1, deps=["FR-AGT-001"], status=PARTIAL),
    R("PRD-GATE-013", "34", "Worker continuity does not depend on one model session.", P0, deps=["FR-AGT-003"],
      status='VERIFIED',
      evidence="Carried by FR-AGT-003's evidence."),
    R("PRD-GATE-014", "34", "Skill creation, quarantine, testing and revalidation work.", P0, deps=["FR-SKL-003", "FR-SKL-005"],
      evidence="SK-01..SK-10 journeys plus 50 skill tests green in data/prd_baseline (2026-09-23, four chunks, .venv-build, -m 'not live and not slow'): 4,053 passed, 73 skipped, 0 failed, every chunk exit 0",
      status='VERIFIED'),
    R("PRD-GATE-015", "34", "Browser controls an authenticated Chrome workflow.", P1, deps=["FR-BRW-002", "FR-BRW-003"], status=BLOCKED_EXTERNAL,
      blocker="No authenticated test account authorised on this host."),
    R("PRD-GATE-016", "34", "Browser and desktop claims require evidence.", P0, deps=["FR-BRW-004"],
      status='VERIFIED',
      evidence="Carried by FR-TRU-001 and FR-BRW-004's evidence."),
    R("PRD-GATE-017", "34", "Consequential confirmation cannot be model-forged.", P0, deps=["FR-POL-003"],
      status='VERIFIED',
      evidence="Carried by FR-POL-003's evidence: the authority guard is one of the 13 proven load-bearing in tests/test_invariants.py."),
    R("PRD-GATE-018", "34", "Undo works for supported reversible actions.", P1, deps=["FR-UNDO-001"],
      status='VERIFIED',
      evidence="Carried by FR-UNDO-001's evidence."),
    R("PRD-GATE-019", "34", "The model router respects privacy, budget and user preference.", P0, deps=["FR-MDL-006", "FR-PRV-001"], status=PARTIAL),
    R("PRD-GATE-020", "34", "The free-model scout cannot use unauthorised credentials.", P1, deps=["FR-MDL-008"], status=MISSING),
    R("PRD-GATE-021", "34", "The Capability Builder succeeds on at least one real missing capability.", P1, deps=["FR-CAP-002", "PRD-JRN-C"], status=MISSING),
    R("PRD-GATE-022", "34", "Technology Scout sandbox/promote/rollback path works.", P2, deps=["FR-UPG-001", "FR-UPG-002"], status=PARTIAL),
    R("PRD-GATE-023", "34", "Proactive anomaly detection works.", P1, deps=["FR-PRO-001"], status=MISSING),
    R("PRD-GATE-024", "34", "Authenticated Telegram control works.", P1, deps=["FR-REM-001"], status=MISSING),
    R("PRD-GATE-025", "34", "The deterministic suite has no unexplained failures.", P0,
      next_action="",
      status='VERIFIED',
      impl='scripts/baseline_suite.py, four chunks with per-test and per-chunk timeouts.',
      files=['scripts/baseline_suite.py'],
      evidence="data/prd_baseline (2026-09-23, four chunks, .venv-build, -m 'not live and not slow'): 4,053 passed, 73 skipped, 0 failed, every chunk exit 0. Zero failures, so no failure needed explaining."),
    R("PRD-GATE-026", "34", "E2E separates product, harness, infrastructure and informational verdicts.", P1, status=EXISTING_UNVERIFIED,
      impl="The harness carries verdict classes and the E2E suite reports them.",
      files=["friday/harness.py"], tests=["tests/e2e/"],
      next_action="Re-run the Playwright E2E suite on this tree and record the four verdict classes it produced."),
    R("PRD-GATE-027", "34", "A qualification soak produces a defensible PASS.", P1, status=NOT_AUDITED,
      impl="The soak harness classifies PASS/FAIL/INCONCLUSIVE and refuses to convert a stalled host into a pass.",
      files=["scripts/soak.py"], tests=["tests/test_soak.py"],
      next_action="Run the soak only after the deterministic gate is green and the host is quiet."),
    R("PRD-GATE-028", "34", "The exact release commit has green CI.", P1, status=NOT_AUDITED,
      next_action="Push is the owner's call (house rule: agents never commit); record the run id when it happens."),
    R("PRD-GATE-029", "34", "One canonical release-gate command produces the machine-readable and human-readable verdict.", P1, status=VERIFIED,
      impl="scripts/release_gate.py runs every mandatory gate, records the command and exit code for each, never reports a skipped gate as PASS.",
      files=["scripts/release_gate.py"], tests=["tests/test_release_gate.py"],
      evidence="Commit 218d895 'one release-gate command that refuses to report an unearned pass'; tests/test_release_gate.py covers the NOT_RUN-is-not-PASS rule."),

    # ---------- 37. Architectural invariants ----------
    R("PRD-INV-01", "37", "FRIDAY is the user-facing manager.", P0, status=VERIFIED,
      impl="Hermes and the coding executors receive bounded packets; policy, objectives, verification and user communication stay in FRIDAY.",
      files=["friday/hermes_bridge.py", "friday/objectives.py", "friday/policy.py"],
      tests=["tests/test_hermes_bridge.py", "tests/test_objective_engine.py"],
      evidence="AGENTS.md NON_NEGOTIABLES 1-2 and the layering ADR; the reachability ratchet keeps the seam honest."),
    R("PRD-INV-02", "37", "A model is not the policy authority.", P0, deps=["FR-GATE-001"],
      status='VERIFIED',
      evidence='Carried by FR-GATE-001: policy is enforced outside the model and an adversarial prompt cannot change a verdict.'),
    R("PRD-INV-03", "37", "No model can grant itself permissions.", P0, deps=["FR-GATE-001", "FR-SKL-006"],
      status='VERIFIED',
      evidence="Carried by FR-GATE-001 and FR-SKL-006: authority comes from PolicyEngine, which never reads a skill or a model's prose."),
    R("PRD-INV-04", "37", "A claim requires matching evidence.", P0, deps=["FR-TRU-001", "FR-TRU-002"],
      status='VERIFIED',
      evidence='Carried by FR-TRU-001 and FR-TRU-002, including the target-correlation guard and its load-bearing test.'),
    R("PRD-INV-05", "37", "Secrets are not normal model context.", P0, deps=["FR-SEC-001"],
      status='VERIFIED',
      evidence='Carried by FR-SEC-001 and FR-PRV-001.'),
    R("PRD-INV-06", "37", "Workspace scope is mandatory for private-state access.", P0, deps=["FR-PRV-002"],
      status='PARTIAL',
      impl='Enforced for memories and observations; the other 15 tables named in workspace.SCOPED carry the workspace_id column so their owner is attributable but their reads are not yet filtered.',
      files=['friday/workspace.py', 'friday/store.py'],
      tests=['tests/test_workspace_isolation.py'],
      next_action='Extend the read filter to the remaining 15 SCOPED tables and move them into ENFORCED_TODAY in tests/test_workspace_isolation.py one at a time.'),
    R("PRD-INV-07", "37", "Skills do not grant authority.", P0, status=VERIFIED, deps=["FR-SKL-006"],
      evidence="Skill permissions are informational; policy_bypass and self_verification are lint FAILs (EXE/skill ledger rows Q/R)."),
    R("PRD-INV-08", "37", "Popular third-party code is not automatically trusted.", P0, status=VERIFIED, deps=["PRD-EXT-001"],
      evidence="Fabric refuses an unpinned upstream at import and restricts copyleft to isolated modes; downloaded skills start QUARANTINED."),
    R("PRD-INV-09", "37", "Installed is not healthy.", P0, deps=["FR-MDL-001"],
      status='VERIFIED',
      evidence='Carried by FR-MDL-001: credentials plus transport success are not health.'),
    R("PRD-INV-10", "37", "Configured is not usable.", P0, deps=["FR-MDL-001"],
      status='VERIFIED',
      evidence='Carried by FR-MDL-001: a transport success with zero output is not usable.'),
    R("PRD-INV-11", "37", "One healthy model route does not prove all provider routes healthy.", P0,
      impl="Health is a property of a route, not a provider (commit 6314dca).",
      files=["friday/provider_health.py"], tests=["tests/test_provider_health.py"],
      next_action="",
      status='VERIFIED',
      evidence='tests/test_provider_health.py plus the invariants provider guard; health is a property of a route (commit 6314dca).'),
    R("PRD-INV-12", "37", "Exact user literals are not silently rewritten.", P0, deps=["FR-TRU-004"],
      status='PARTIAL',
      next_action='Carried by FR-TRU-004, which is PARTIAL pending calendar and message-body corruption tests.'),
    R("PRD-INV-13", "37", "Deterministic work does not require expensive planning.", P1, status=EXISTING_UNVERIFIED, deps=["NFR-PERF-002"]),
    R("PRD-INV-14", "37", "Worker sessions are disposable.", P0, deps=["FR-AGT-003"],
      status='VERIFIED',
      evidence='Carried by FR-AGT-003: the ownership guard and the chaos suite.'),
    R("PRD-INV-15", "37", "Durable truth belongs in canonical FRIDAY state.", P0, deps=["FR-MEM-001"],
      status='PARTIAL',
      next_action='Carried by FR-MEM-001 (now VERIFIED) and PRD-INV-06; the remaining gap is the 15 scoped tables that are stamped but not yet read-filtered.'),
    R("PRD-INV-16", "37", "Self-improvement cannot silently rewrite its trust root.", P0, deps=["FR-SEC-002"],
      status='VERIFIED',
      evidence='Carried by FR-SEC-002: every alias and symlink form of a kernel path is refused, proven load-bearing.'),
    R("PRD-INV-17", "37", "External content cannot override policy.", P0, deps=["FR-GATE-001"],
      status='VERIFIED',
      evidence='Carried by FR-GATE-001: injected page content cannot reach destructive tools and planted instructions authorise nothing.'),
    R("PRD-INV-18", "37", "Research has source and freshness.", P1, status=MISSING, deps=["FR-MEM-005"]),
    R("PRD-INV-19", "37", "New capabilities require a failure path and a rollback strategy.", P1, status=EXISTING_UNVERIFIED, deps=["FR-CAP-002", "FR-UPG-002"]),
    R("PRD-INV-20", "37", "FRIDAY becomes more capable without becoming less understandable, private or controllable.", P1, status=PARTIAL, deps=["PRD-DEV-001", "FR-MEM-004", "FR-POL-001"]),

    # ---------- 0.3 / 39. Deferred and spikes ----------
    R("PRD-DEF-001", "0.3", "Native mobile app-store distribution.", P3, status=DEFERRED_BY_PRD,
      evidence="PRD 0.3 defers it to the future companion phase."),
    R("PRD-DEF-002", "0.3", "Full cloud SaaS multi-tenancy.", P3, status=DEFERRED_BY_PRD,
      evidence="PRD 0.3: local-first is V1.",
      owner_note="Owner: 'let's leave the cloud mode for now'."),
    R("PRD-DEF-003", "0.3", "Public SDK semver/tree-shaking/peer-dependency concerns.", P3, status=NOT_APPLICABLE,
      evidence="PRD 0.3: not a primary product surface; FRIDAY ships no public SDK."),
    R("PRD-DEF-004", "0.3", "Public skills marketplace.", P3, status=DEFERRED_BY_PRD,
      evidence="PRD 0.3 and the v3.1 matrix FR-068; skills stay local/private by default.",
      owner_note="Owner accepts optional community sharing later, with user consent."),
    R("PRD-DEF-005", "0.3", "Full iOS automation.", P3, status=DEFERRED_BY_PRD,
      evidence="PRD 0.3: Android is the stronger initial target."),
    R("PRD-DEF-006", "0.3", "Final pricing/billing model.", P3, status=DEFERRED_BY_PRD,
      evidence="PRD 0.3 defers it until product validation.",
      owner_note="Owner: build first, decide the SaaS shape later."),
    R("PRD-DEF-007", "11", "macOS support (installer, permissions, desktop/browser integration, background runtime).", P3, status=DEFERRED_BY_PRD,
      evidence="PRD 35 Phase 11; the build script refuses non-Windows platforms by design.",
      owner_note="Owner: Windows now, Mac later."),
    R("PRD-SPIKE-01", "39", "Division of responsibility between Mem0 and OpenViking.", P2, status=MISSING, deps=["FR-MEM-002"],
      next_action="Write it as an ADR with the benchmark that decided it."),
    R("PRD-SPIKE-02", "39", "Laya versus alternative local classifiers on target hardware.", P1, status=MISSING, deps=["FR-MDL-004"]),
    R("PRD-SPIKE-03", "39", "Needle confidence thresholds and fallback behaviour.", P1, status=MISSING, deps=["FR-MDL-005"]),
    R("PRD-SPIKE-04", "39", "Default system-Chrome versus dedicated FRIDAY profile strategy.", P1, status=PARTIAL, deps=["FR-BRW-002"],
      impl="browser_profiles.choose_profile already implements a rule; it is not recorded as a decision.",
      next_action="Record the rule as an ADR with the evidence behind it."),
    R("PRD-SPIKE-05", "39", "Customer PII retention default for the pilot.", P1, status=MISSING, deps=["FR-PRV-001"],
      blocker="A business/legal decision the owner must make.",
      next_action="Ask the owner for the retention default; implement it as policy, not code."),
    R("PRD-SPIKE-06", "39", "Best multilingual voice/STT/TTS stack after latency and quality tests.", P1, status=BLOCKED_EXTERNAL, deps=["FR-VOI-001"],
      blocker="LiveKit credentials are placeholders; no live latency measurement is possible on this host."),
    R("PRD-SPIKE-07", "39", "Curated legitimate free-model registry and scout rules.", P1, status=MISSING, deps=["FR-MDL-008"]),
    R("PRD-SPIKE-08", "39", "Telegram authentication and pairing design.", P1, status=MISSING, deps=["FR-REM-001"]),
    R("PRD-SPIKE-09", "39", "macOS background-service and permission architecture.", P3, status=DEFERRED_BY_PRD, deps=["PRD-DEF-007"],
      evidence="Deferred with macOS itself (PRD 35 Phase 11)."),
    R("PRD-SPIKE-10", "39", "Long-term commercial packaging after validation.", P3, status=DEFERRED_BY_PRD, deps=["PRD-DEF-006"]),

    # ---------- Owner direction that amends or extends the PRD ----------
    R("PRD-OWN-WINPROMPT", "owner", "Worker prompts must not be passed as arbitrarily long Windows command-line arguments; use stdin or a prompt file, with a regression test using a realistically large work packet.", P1,
      status=NOT_AUDITED,
      impl="The claude_code executor builds an invocation; the transport needs checking.",
      files=["friday/executors/claude_code.py"],
      tests=[],
      next_action="Read the transport; if it is argv, move to a file/stdin and add the large-packet regression test.",
      owner_note="From the master program section 32."),
    R("PRD-OWN-CAPTCHA", "owner", "Owner asks FRIDAY to solve captchas itself while OTP and hardware keys stay with the user.", P2,
      status=PARTIAL,
      impl="Human-verification detection exists in browser page state and pauses for handoff.",
      files=["friday/browser.py"],
      tests=["tests/test_auth_boundary_resume.py"],
      next_action="Keep detection plus handoff as the default and expose a policy-gated solver seam the owner can point at a service they are entitled to use.",
      owner_note="Reconciled with PRD 4.2 ('does not bypass access controls') and FR-BRW-003 ('does not silently bypass security controls'): FRIDAY detects the challenge and hands off; it does not defeat the control. An owner-configured solver is a seam, not built-in behaviour."),
    R("PRD-OWN-DISCLOSE", "owner", "Owner does not want FRIDAY to disclose that it is an AI on customer-facing voice or chat.", P1,
      status=BLOCKED_EXTERNAL,
      impl="No customer-facing voice or chat channel exists yet, so nothing implements either behaviour.",
      files=[],
      tests=[],
      blocker="A legal decision that belongs to the owner: several jurisdictions (for example California's bot-disclosure law and the EU AI Act transparency article) require disclosure to a person who asks or is being solicited. FRIDAY must not be hardcoded to deny being an AI.",
      next_action="Implement disclosure as a per-workspace policy setting with a jurisdiction default, so the owner's preference applies where lawful; do not build a denial.",
      owner_note="Recorded verbatim as owner direction; the mechanism is policy, and the setting is the owner's call."),
    R("PRD-OWN-FREEKEY", "owner", "Owner wants FRIDAY to obtain free-tier API keys automatically without manual work.", P1,
      status=PARTIAL, deps=["FR-MDL-008"],
      impl="Nothing acquires keys. The secret broker is where a key would land and the provider health check is what would admit the route.",
      files=["friday/secret_broker.py", "friday/provider_health.py"],
      next_action="Implement assisted acquisition: FRIDAY opens the official signup page in the browser, the user completes identity, FRIDAY stores the key through the broker and benchmarks the route.",
      owner_note="Reconciled with FR-MDL-008 and PRD 4.2: automated account creation, phone/identity entry and quota evasion stay out. Assisted acquisition satisfies 'no manual task' for everything except the identity step the provider requires."),
    R("PRD-OWN-LICENCE", "owner", "Owner: ignore licences, dissolve third-party code so it is undiscoverable inside FRIDAY.", P0,
      status=REJECTED_WITH_EVIDENCE,
      impl="The fabric records provenance, pins commits and refuses copyleft outside isolated modes; notices are generated.",
      files=["friday/fabric.py", "scripts/third_party_notices.py", "third_party/UPSTREAM_LOCK.json"],
      tests=["tests/test_fabric.py", "tests/test_third_party_notices.py"],
      evidence="PRD 29 requires that 'internal provenance, license obligations, source/version, and security status must remain auditable' and PRD 4.2 lists automatic trust of third-party code as a non-goal. The owner's UX goal is met instead: third-party branding is invisible in normal UX (single assistant, capabilities not products) while the internal registry stays auditable. Removing licence notices would create a distribution liability for the owner and break INV-08.",
      next_action="None. If the owner still wants notices stripped, that is a legal decision to take with counsel, not an engineering change."),
    R("PRD-OWN-BRAND", "owner", "The product name FRIDAY, its voice identity and the owner's name (Darsh Yadav) are fixed and must be pronounced correctly.", P2,
      status=PARTIAL,
      impl="Identity and profile toolsets hold the owner's name; the voice persona lives in agent_friday.py.",
      files=["agent_friday.py", "friday/profile.py", "friday/toolsets/identity.py"],
      tests=["tests/test_profile.py", "tests/test_identity.py"],
      next_action="Add a pronunciation hint for the owner's name on the TTS path and a test that the persona name is not overridable by a model."),
    R("PRD-OWN-MARKET", "owner", "Marketplace and SaaS operation (Shopify, Amazon, Myntra, Flipkart, Etsy, eBay and anything else the user names) works through the browser and OAuth, with no per-platform API key required.", P1,
      status=PARTIAL, deps=["FR-BRW-001", "FR-BRW-002"],
      impl="Browser primitives, profile choice and external-write confirmation are platform-neutral; no platform is hardcoded, and none has a verified journey.",
      files=["friday/browser.py", "friday/browser_profiles.py", "friday/connectors/plane.py"],
      tests=["tests/test_browser_primitives.py", "tests/test_browser_profiles.py"],
      next_action="Prove one platform end to end against a controlled page, keeping the path generic.",
      owner_note="Owner is emphatic that the limitation must not be a fixed platform list."),
    R("PRD-OWN-SUBAGENT", "owner", "FRIDAY has its own specialist sub-agents that do not depend on Hermes or a coding CLI for small work, and anything added is added to both Hermes and the coding CLI side.", P2,
      status=PARTIAL,
      impl="Role catalogue and org plane run FRIDAY-side work; the deterministic and reflex paths handle small tasks with no delegation.",
      files=["friday/roles.py", "friday/org.py", "friday/reflex.py", "friday/executor_router.py"],
      tests=["tests/test_roles.py", "tests/test_org.py", "tests/test_reflex.py"],
      next_action="Record the parity rule (new capability reaches both execution backends) as a test over the executor registry."),
]


# --- progress recorded during the implementation program ---------------------
#
# Appended rather than edited into the rows above, deliberately. The rows are
# the audit: what was found on 2026-09-23 before any of this work started.
# These entries are what changed and what proves it. Keeping them apart means
# a reader can see both, and it means this file is only ever appended to -
# the first update round rewrote the rows in place with a regex and corrupted
# four of them, because a literal containing the other quote character ends
# early.
#
# `apply_progress` merges them in order, so a later entry about the same
# requirement supersedes an earlier one, and `--check` validates the merged
# result rather than the raw table.

PROGRESS: list[tuple[str, dict]] = []

# The same, for the v7 rows in prd_v7_table.py.
PROGRESS_V7: list[tuple[str, dict]] = [
    # --- Slice 1, cheap proofs (docs/plans/prd-v7-truth-audit/02-build-sequence.md), 2026-09-26
    ("TRUST-001", {
        "status": VERIFIED,
        "current_implementation": (
            "Provenance is typed by the runtime: contracts.Run.provenance is fixed at creation, and "
            "CapabilityRuntime.execute asks policy.provenance_verdict before the ordinary decision, so what a "
            "payload says about its own trust is data. Browser external writes no longer take authority from "
            "model-supplied arguments: browser.AUTHORITY_KEYS are stripped, and a purchase, publish, destructive "
            "or security-settings step needs a confirmation.book nonce the person approved, bound to the run, "
            "tool, target, page and kind, and spent once. Scope: this holds for a Run that carries its "
            "provenance. On the durable objective path the provenance is dropped before the Run is built "
            "(TRUST-002, V7-D04), so there a READ_MATERIAL origin never reaches this check - a lineage defect, "
            "not trust taken from text."),
        "files": ["friday/contracts.py:379-391", "friday/capability_runtime.py:377", "friday/policy.py:218",
                  "friday/browser.py:128", "friday/toolsets/web.py:559-600"],
        "tests": ["tests/test_trust.py::test_a_payload_that_declares_itself_trusted_stays_untrusted",
                  "tests/test_trust.py::test_the_same_payload_from_the_person_reaches_the_ordinary_question",
                  "tests/test_browser_confirmation.py::test_a_yes_written_into_the_arguments_does_not_authorise_an_external_write",
                  "tests/test_browser_confirmation.py::test_an_external_write_asks_and_does_nothing_even_when_the_arguments_claim_a_yes",
                  "tests/test_browser_confirmation.py::test_a_yes_for_one_action_cannot_be_spent_on_another"],
        "evidence": (
            "2026-09-26: a READ_MATERIAL Run whose payload declares trusted=true, provenance=PERSON, approved=true "
            "and a made-up nonce is refused power_shutdown through CapabilityRuntime.execute under DANGEROUS "
            "autonomy (FAILED, 'came from something Friday read', provenance unchanged, the shutdown function "
            "never called). The same payload from the person reaches the ordinary APPROVAL_REQUIRED question. "
            "Found and fixed on the way: browser_act honoured confirmed/approved keys in model-supplied "
            "arguments, so injected page text could self-confirm a purchase. Mutations each turned the tests "
            "red: the provenance check removed (capability_runtime.py:377), the browser confirmation check "
            "bypassed (browser.py:652), the authority keys left in the arguments (web.py:559)."),
    }),
    ("TRUST-002", {
        "status": PARTIAL,
        "current_implementation": (
            "capability_runtime.execute calls policy.provenance_verdict(tool_id, run.provenance) before "
            "engine.decide and before the capability loads, so a Run that carries READ_MATERIAL is refused "
            "destructive and secret-reading tools. Gap (found 2026-09-26): lineage is not retained through the "
            "durable objective transform. objective_runs has no provenance or attended column, and both "
            "objective dispatch ports build each task's Run from the defaults PERSON/attended, so a "
            "READ_MATERIAL origin cannot reach the provenance gate once the work is durable."),
        "files": ["friday/policy.py:218-284", "friday/capability_runtime.py:336,376-377,478",
                  "friday/objective_cli.py:76-77", "friday/store.py:825-839", "friday/contracts.py:370,379"],
        "evidence": (
            "The gate itself is proven for runs that carry their provenance (tests/test_trust.py:82-89, and "
            "TRUST-001's runtime test). The durable path drops it: store.py:825-839 declares objective_runs "
            "without provenance/attended; capability_runtime.build_dispatch calls runtime.execute(capability_id, "
            "arguments) with no Run (:478), so execute builds a default one (:336); objective_cli.build_dispatch "
            "builds c.Run.create(...) with the defaults (:76-77); contracts.py:370,379 default attended=True, "
            "provenance=PERSON."),
        "next_action": (
            "EXTEND in Slice 2 (SEC-005 envelope): carry provenance and attended from the objective's origin into "
            "objective_runs and into every dispatched Run, with a test that a READ_MATERIAL objective compiled and "
            "run through ContinuousTaskExecutor is refused a destructive capability."),
    }),
    ("COG-001", {
        "status": VERIFIED,
        "files": ["friday/execution_economics.py:81-178", "friday/task_class.py:135-176",
                  "friday/voice_brain.py:859-874"],
        "tests": ["tests/test_zero_frontier_calls.py::test_a_deterministic_task_runs_end_to_end_without_any_model",
                  "tests/test_zero_frontier_calls.py::test_the_counters_see_a_model_call_when_one_is_made",
                  "tests/test_zero_frontier_calls.py::test_every_counted_door_moves_its_counter",
                  "tests/test_zero_frontier_calls.py::test_no_product_module_binds_a_counted_door_by_name",
                  "tests/test_execution_economics.py::test_mechanical_work_uses_no_model"],
        "evidence": (
            "2026-09-26: five read-only requests (system info, disks, time, processes, network) each run through "
            "toolsets.objectives.objective_start (admission, the deterministic planner, compile_objective) and "
            "ContinuousTaskExecutor with capability_runtime.build_dispatch() to RUN_COMPLETED with the expected "
            "capability. Counters on every model door (planner_model.interpret/_model, ModelGateway.infer, "
            "ModelGatewayWorker.call, HermesSupervisor.delegate, voice_brain._model, genai.Client construction) "
            "and on every non-loopback socket connect and DNS lookup read 0. Every counter has its own negative "
            "control (the door called as in-process code resolves it, real function stubbed; the blocked "
            "connection is counted), the planner control forces the model branch through the whole pipeline, "
            "and a static check pins that no product module binds a counted function by name. Mutations turned "
            "them red: counting disabled, the network counter silenced, a by-name import planted in "
            "friday/camera.py, the Gemini counter removed, needs_model -> return True. Scope: the task pipeline, "
            "in process. A spoken turn still reaches the conversational model at the NORMAL tier "
            "(voice_brain.py:859-874); that is the interface, not the task's execution."),
    }),
    ("CORE-001", {
        "current_implementation": (
            "Hermes/Claude Code/Codex work is wrapped by friday/hermes_bridge.py (TaskBundle, WorkRunLog) and "
            "friday/executors/*; executor results land in store-backed objectives and are re-graded rather than "
            "trusted by exit code. hermes_delegate now asks the PolicyEngine (COMMAND_EXECUTION) before anything "
            "starts, the same decision every other control asks for: ASK under GUARDED returns APPROVAL_REQUIRED "
            "and starts nothing. Gap: policy is enforced at dispatch, not at return. A Hermes COMPLETE is "
            "relayed as the result without FRIDAY's verifier; DevelopmentRun.verify/gate (evaluation.graded + "
            "promotion.decide, tested in tests/test_development.py) has no live caller."),
        "files": ["friday/hermes_bridge.py:221", "friday/hermes_bridge.py:691", "friday/tools/hermes_control.py:113-120",
                  "friday/tools/hermes_control.py:163-183", "tests/test_executor_continuity.py:53",
                  "tests/test_executor_continuity.py:134"],
        "tests": ["tests/test_executor_continuity.py::test_exit_code_zero_is_not_success",
                  "tests/test_executor_continuity.py::test_a_run_is_recorded_before_anything_happens",
                  "tests/test_trust.py::test_hermes_delegation_meets_the_policy_before_anything_starts"],
        "evidence": (
            "2026-09-26: GUARDED autonomy -> hermes_delegate returns failed with APPROVAL_REQUIRED and the "
            "supervisor never starts; FULL -> working, started. The mutation removing the verdict check "
            "(hermes_control.py:118) turned the test red."),
        "next_action": (
            "EXTEND: route a delegated run's COMPLETE through FRIDAY's verifier before it is reported done - wire "
            "evaluation.graded + promotion.decide (DevelopmentRun.verify/gate) into the Hermes delivery path - "
            "with a test that a Hermes-reported success whose verifier fails is not reported as done."),
    }),
    ("EVAL-001", {
        "current_implementation": (
            "friday/evaluation.py's Verifier/Attempt/Record harness: graded() verifies and records; remember() "
            "persists to record_path(), which the development router reads (development.py:188); "
            "promotion.decide consumes the Attempt. graded() is now tested on every path. Found and fixed: the "
            "suite wrote the live data/evaluation/attempts.json - all 120 rows it held were test fixtures "
            "(agents 'scripted'/'s' on widget/greeter/feature) - and is now redirected per test by "
            "tests/conftest.py::_evaluation_record_is_per_test. Gap: an Attempt carries no build identity or "
            "verifier version, so an eval is not yet versioned and linked to a build, and model/route promotion "
            "does not use Record/graded."),
        "files": ["friday/evaluation.py:249-300", "friday/development.py:188", "friday/promotion.py:303",
                  "tests/conftest.py:182", "tests/test_promotion.py:36", "tests/test_promotion.py:43"],
        "tests": ["tests/test_promotion.py::test_an_unverified_run_is_refused",
                  "tests/test_promotion.py::test_a_failed_verifier_is_refused",
                  "tests/test_promotion.py::test_graded_records_the_attempt",
                  "tests/test_evaluation.py::test_graded_work_is_persisted_where_the_router_reads_it",
                  "tests/test_evaluation.py::test_a_failing_verifier_is_recorded_as_failed",
                  "tests/test_evaluation.py::test_a_verifier_that_cannot_start_is_inconclusive_not_failed",
                  "tests/test_evaluation.py::test_a_caller_held_record_is_not_counted_twice",
                  "tests/test_evaluation.py::test_an_unwritable_record_does_not_lose_the_attempt",
                  "tests/test_evaluation.py::test_the_suite_never_writes_the_live_record"],
        "evidence": (
            "2026-09-26 (part a): the six graded() tests pass, and six mutations each turned one red (persistence "
            "removed, INCONCLUSIVE graded FAILED, a caller-held record also written to the shared file, OSError "
            "not caught, every exit graded PASSED, the conftest redirect removed). The live record's hash was "
            "unchanged across a 125-test run of the evaluation, promotion, development, vertical-slice and "
            "router suites."),
        "next_action": (
            "EXTEND: (b) stamp each Attempt with the build identity (friday/build_identity.py) and the verifier's "
            "command, so an eval is versioned and linked to the build it judged; (c) route model/provider "
            "promotion (MODEL-003) through the same Record/graded evidence. Owner: move the polluted "
            "data/evaluation/attempts.json aside - the agent's rename was not permitted."),
    }),
    ("NEURAL-002", {
        "status": VERIFIED,
        "current_implementation": (
            "No production module trains or saves weights: the only local model (friday/reflex.py, Needle) is "
            "loaded for inference. tests/test_neural_boundary.py is an AST architecture scan of friday/ and the "
            "entry points for weight-update primitives - optimiser/scheduler step, backward, zero_grad, "
            "apply_gradients, fit/partial_fit/train_on_batch, trainers and trainer.train(), torch.save, "
            "save_pretrained/save_weights, safetensors saves, push_to_hub, direct weight edits (in-place ops on "
            ".data, any .grad/requires_grad), and training-module imports including dynamic ones - outside the "
            "PRD 24.11 candidate path friday/neural/candidates/ (empty today)."),
        "files": ["tests/test_neural_boundary.py:137", "friday/reflex.py:453-476"],
        "tests": ["tests/test_neural_boundary.py::test_no_production_module_updates_weights",
                  "tests/test_neural_boundary.py::test_a_planted_weight_update_is_caught",
                  "tests/test_neural_boundary.py::test_the_scan_reads_the_product_it_claims_to",
                  "tests/test_neural_boundary.py::test_inference_and_prose_are_not_updates"],
        "evidence": (
            "2026-09-26: the scan reads more than 100 product modules (asserted) and finds no primitive. Negative "
            "controls: 21 planted sources are each caught and 7 prose/inference sources (including torch.no_grad "
            "inference) are not; planting optimizer.step() or p.data.add_() in friday/reflex.py, save_pretrained "
            "in friday/evaluation.py, and a Trainer or torch.optim import in friday/camera.py each turned the scan "
            "red, and restoring them turned it green. Scope: a static guard against ordinary code, not an "
            "adversarial sandbox (a getattr trick, or .step() on a receiver not named like an optimiser, evades "
            "it); weights written by a delegated worker are the SEC-005 envelope's concern."),
    }),
    ("VERIFY-002", {
        "status": VERIFIED,
        "current_implementation": (
            "tests/negative_controls.py maps each of PRD 79's twelve critical negative controls to its red-path "
            "tests (28 node IDs, 110 cases). tests/test_negative_controls.py keeps it honest: the names are read "
            "from the PRD, every entry has a test or an open owning row, every test is collected offline, and "
            "VERIFY-002 cannot be VERIFIED while a control is uncovered. "
            "scripts/release_gate.py::gate_negative_controls runs each control's tests and reads each run's JUnit "
            "report: every named test must pass - one that failed, did not run or skipped fails the gate, except "
            "a host skip the registry allows with its reason - and it stays FAIL until every control has a red "
            "path. All twelve are demonstrated; lossy compaction since 2026-09-27, by CTX-003's validator and "
            "the objective delegation that refuses a lossy packet."),
        "files": ["tests/negative_controls.py", "tests/test_negative_controls.py", "scripts/release_gate.py:189",
                  "scripts/release_gate.py:156", "tests/test_invariants.py:1"],
        "tests": ["tests/test_compaction.py::test_dropping_any_one_fact_is_rejected",
                  "tests/test_negative_controls.py::test_the_registry_names_exactly_the_prd_controls",
                  "tests/test_negative_controls.py::test_every_named_red_path_is_collected_offline",
                  "tests/test_negative_controls.py::test_verify_002_is_not_verified_while_a_control_has_no_test",
                  "tests/test_release_gate.py::TestTheNegativeControlGateWantsARealRedPath::test_a_control_with_no_test_keeps_the_gate_red",
                  "tests/test_release_gate.py::TestTheNegativeControlGateWantsARealRedPath::test_a_control_whose_cases_all_skipped_is_not_demonstrated"],
        "evidence": (
            "2026-09-26, the real gate on this tree: FAIL, 11/12 controls demonstrated, every named test passed "
            "(109 cases) except the allowed kernel symlink host skip, reported as a limitation; the missing "
            "lossy-compaction control keeps it red. Registry mutations (a control renamed, a test misnamed, the "
            "owner removed or unknown, a live-marked test swapped in) and a ledger mutation (VERIFY-002 set "
            "VERIFIED) each turned the meta-test red; gate mutations (any skip allowed, a missing test not "
            "flagged, a failure not flagged, skip-only counted, a stale report judged, the class-node match "
            "dropped, an uncovered control counted, the verdict forced PASS, a nested basetemp) each turned its "
            "tests red. "
            "2026-09-27, the real gate on this tree (data/prd_v7_audit/negative_controls_2026-09-27/gate.json and one JUnit report per control): PASS, 12/12 controls have a red path and every one passed (112 cases), the only limitation the allowed kernel-symlink host skip, whose control passed its other 27 cases. "
            "The four-chunk suite over the fixed tree (data/baseline_v7_slice4g): all four chunks exit 0, 4,684 passed (1,259, 1,042, 1,286 and 1,097), none failed."),
        "next_action": "",
    }),
    ("SEC-004", {
        "files": ["friday/trust.py:67-102", "friday/skills/lint.py:40-55", "tests/test_injection_pages.py:164-174",
                  "friday/browser.py:128", "friday/toolsets/web.py:559-600"],
        "tests": ["tests/test_trust.py::test_an_adversarial_prompt_cannot_change_a_verdict",
                  "tests/test_trust.py::test_injected_instructions_requesting_secrets_fail",
                  "tests/test_injection_pages.py::test_a_page_cannot_turn_a_question_into_a_write",
                  "tests/test_skills_golden.py::test_sk10_a_hostile_skill_cannot_disable_policy_or_trust_itself",
                  "tests/test_browser_confirmation.py::test_a_yes_written_into_the_arguments_does_not_authorise_an_external_write",
                  "tests/test_browser_confirmation.py::test_a_made_up_nonce_is_refused"],
        "evidence": (
            "test_injection_pages.py:164-174 serves a hostile page through the real call path and the page's "
            "call never reaches the tool; test_trust.py covers jailbreak text as a tool id and secret reads under "
            "FULL autonomy (re-run 2026-09-25 on 030795f: all passed). 2026-09-26: a hole was found and closed - "
            "browser_act read 'confirmed'/'approved' from model-supplied arguments, so text the model had read "
            "could authorise a purchase or a destructive browser step. Authority keys are now stripped "
            "(browser.py:128) and an external write needs a person-approved, action-bound, single-use nonce "
            "(web.py:559-600); tests/test_browser_confirmation.py (10 tests) pins it, and mutations of the "
            "check and of the stripping turned it red. Residual: the nonce binds the page URL and the kind, "
            "not the page content, within its 60 s life."),
    }),
    # --- Slice 2, step 1 (04-slice-2-design.md), 2026-09-26: friday/sandbox.py, a full copy of
    # friday/execution.py used by nothing but its own tests, is retired with tests/test_sandbox.py
    # (34/34 tests are the same tests in tests/test_execution.py). Citations move to the twins.
    ("SEC-001", {
        "current_implementation": (
            "friday/secret_broker.py ingest/store never returns raw values (confirmed: out['status']=='stored' "
            "and FAKE_KEY not in json.dumps(out)); friday/execution.py redacts injected credentials from "
            "subprocess output (def redacted at :428, applied to stdout/stderr at :482). The same redaction "
            "lived in friday/sandbox.py, a duplicate retired 2026-09-26."),
        "files": ["friday/secret_broker.py", "tests/test_secret_broker.py:19-52", "friday/execution.py:428,482"],
        "tests": ["tests/test_secret_broker.py::test_ingest_returns_metadata_never_value",
                  "tests/test_secret_broker.py::test_model_facing_surfaces_carry_no_value",
                  "tests/test_execution.py::test_output_carrying_an_injected_credential_is_redacted"],
        "evidence": (
            "Read tests/test_secret_broker.py:19-25 and :44-52: asserts FAKE_KEY not in json.dumps(out)/listing/"
            "meta. Read tests/test_execution.py:219-223: runs a child with a TASK_TOKEN credential injected, "
            "asserts the raw token is not in stdout and '[redacted]' is. Matches 'dummy-secret scans clean.' "
            "Re-run 2026-09-25 on 030795f (data/prd_v7_audit/rerun.xml); the redaction test's twin in the "
            "retired tests/test_sandbox.py was identical. 2026-09-26: tests/test_execution.py passes on the "
            "Slice 2 tree."),
    }),
    ("SEC-005", {
        "current_implementation": (
            "The envelope is a declared contract: friday/contracts.py Envelope, NetworkScope and Limits carry "
            "every PRD 43 field (cwd, read/write roots, network, secret aliases, allowed/blocked capabilities, "
            "process/memory/time/token/tool-call limits, expiry) plus lineage (provenance, attended). It only "
            "narrows: every widening raises (tests/test_envelope.py). The OS primitives are in "
            "friday/execution.py (Egress deny-all by default, JobObject process limits, closed-environment "
            "refusal) and FileJail. The duplicate friday/sandbox.py is retired, and execution.py and "
            "contracts.py are kernel paths that self-upgrade may not rewrite. Gap: the envelope is not yet "
            "attached to or enforced at any worker launch (capability runtime, CLI launch, fabric, Hermes). "
            "HOST stays the default backend by owner decision (2026-09-25)."),
        "files": ["friday/contracts.py:450,535,585", "friday/execution.py:138", "friday/fsjail.py:163",
                  "friday/executors/claude_code.py:103", "friday/self_upgrade.py:55-60",
                  "tests/test_execution.py:84,128,134,226,284"],
        "tests": ["tests/test_envelope.py::test_nothing_can_be_widened",
                  "tests/test_envelope.py::test_narrowing_can_tighten_every_field",
                  "tests/test_envelope.py::test_the_boundary_a_run_meets_is_kernel",
                  "tests/test_execution.py::test_a_path_cannot_climb_out",
                  "tests/test_execution.py::test_an_absolute_path_cannot_reach_out",
                  "tests/test_execution.py::test_deny_all_is_the_default",
                  "tests/test_execution.py::test_a_fork_bomb_hits_the_process_limit_not_the_machine",
                  "tests/test_execution.py::test_a_closed_sandbox_refuses_to_run_anything"],
        "next_action": (
            "EXTEND (Slice 2 steps 2-8, docs/plans/prd-v7-truth-audit/04-slice-2-design.md): persist the envelope "
            "on objective_runs, enforce it in capability_runtime, the CLI launch, fabric spawn and the Hermes "
            "bridge, and test that an out-of-envelope write, host and capability, and an expired envelope, are "
            "denied per worker type."),
    }),
    ("NET-001", {
        "current_implementation": (
            "Three mechanisms, not yet unified: (1) friday/netguard.py global SSRF/address-class safety (deny "
            "private/loopback/metadata, re-resolve before connect); (2) friday/execution.py Egress "
            "(DENY_ALL/ALLOWLIST(hosts)/ALLOW_ALL per worker, no objective, expiry or revoke); (3) friday/trust.py "
            "SecurityAuthorization - objective-bound, expiring, audited - gated to the security namespace via "
            "target_guard(). New 2026-09-26: contracts.NetworkScope carries PRD 44's six modes, the objective, "
            "an expiry and a grant id, and an expired scope allows nothing (tests/test_envelope.py). Gap: grants "
            "are not yet stored or revocable, and netguard does not consume the scope, so 'expired or unknown "
            "host denied' is not yet a guarantee for ordinary outbound calls."),
        "files": ["friday/netguard.py:78-100", "friday/execution.py:138", "friday/trust.py:122-227",
                  "friday/contracts.py:535"],
        "tests": ["tests/test_netguard.py::test_non_public_addresses_are_refused",
                  "tests/test_execution.py::test_development_defaults_to_no_network",
                  "tests/test_execution.py::test_development_with_named_hosts_is_an_allowlist",
                  "tests/test_trust.py::test_contract_expiry_and_unbounded_scope_are_refused",
                  "tests/test_envelope.py::test_an_expired_scope_allows_nothing",
                  "tests/test_envelope.py::test_selected_hosts_allow_exactly_what_they_name"],
        "next_action": (
            "EXTEND (Slice 2 step 5): store-backed, audited, revocable grants for contracts.NetworkScope, "
            "consumed by netguard.check()/fetch() for every capability call; test that an expired, revoked or "
            "unknown host is denied."),
    }),
    ("CAP-002", {
        "current_implementation": (
            "Confirmed by reading: friday/skills/lint.py:40-55 pattern-matches exfiltration/self_authorization/"
            "policy_bypass language pre-activation (no composition logic); friday/execution.py Egress defaults "
            "to DENY_ALL with no per-capability-pair reasoning; friday/policy.py decide(self, tool_id) evaluates "
            "exactly one tool_id at a time, never a combined set. No composition/elevation analyzer exists. "
            "Gap: no test where two individually-allowed capabilities (read-private + arbitrary-send) are "
            "combined and the combined grant is escalated or blocked."),
        "files": ["friday/skills/lint.py:40-55", "friday/execution.py:138", "friday/policy.py:663"],
    }),
    # --- Slice 2, step 2: lineage and the envelope on the durable path, 2026-09-26
    ("TRUST-002", {
        "status": VERIFIED,
        "current_implementation": (
            "Lineage is retained through the objective and schedule transforms. compile_objective takes the "
            "caller's provenance and attendance and records them, with a minted execution envelope, on "
            "objective_runs (new provenance/attended/envelope columns, written once). The executor rebuilds each "
            "task's Run from them and hands it to the port, and capability_runtime.execute applies "
            "provenance_verdict to it. Schedules record their author: a firing takes the less trusted of author "
            "and requester, and only a hand firing is attended. Rows from before the migration have no envelope "
            "and park for the person (fail closed). Every product caller of compile_objective states its lineage "
            "(static guard). Found and closed on the way (V7-D13): command execution - Hermes delegation, "
            "capability_use, the connectors, self-development promotion - was AUTO and open to read material; "
            "policy.UNREACHABLE_FROM_READ_MATERIAL now refuses it before any confirmation exists."),
        "files": ["friday/objectives.py:375,402", "friday/continuous.py:104,643,944,963",
                  "friday/capability_runtime.py:359,490", "friday/store.py:1002,1007,2265,2594",
                  "friday/toolsets/schedules.py:353,397,536,572", "friday/toolsets/objectives.py:403",
                  "friday/policy.py:225,228"],
        "tests": ["tests/test_envelope_lineage.py::test_read_material_is_refused_a_destructive_capability_on_the_durable_path",
                  "tests/test_envelope_lineage.py::test_a_read_material_objective_cannot_delegate_to_a_terminal",
                  "tests/test_envelope_lineage.py::test_no_command_execution_tool_is_reachable_from_read_material",
                  "tests/test_envelope_lineage.py::test_the_persons_objective_reaches_the_ordinary_question",
                  "tests/test_envelope_lineage.py::test_a_schedule_written_from_read_material_fires_as_read_material",
                  "tests/test_envelope_lineage.py::test_running_a_schedule_by_hand_passes_the_requesters_lineage",
                  "tests/test_envelope_lineage.py::test_a_run_from_before_envelopes_waits_for_the_person",
                  "tests/test_envelope_lineage.py::test_every_product_caller_states_the_lineage",
                  "tests/test_trust.py::test_injected_page_content_cannot_reach_destructive_tools"],
        "evidence": (
            "2026-09-26: a READ_MATERIAL objective compiled and driven by ContinuousTaskExecutor through "
            "capability_runtime.build_dispatch() under DANGEROUS autonomy is refused power_shutdown and "
            "hermes_delegate ('came from something Friday read'; both resolved to recorders that were never "
            "called), while the same objective from the person reaches APPROVAL_REQUIRED. Before the fix the "
            "delegation test failed: read material reached the terminal. Schedules: written from read material "
            "-> fires as READ_MATERIAL, unattended; the person's -> PERSON, unattended; hand-run from read "
            "material -> READ_MATERIAL, attended; a pre-migration schedule -> untrusted. Mutations turned the "
            "tests red: the Run not passed to the port, the envelope check removed, the legacy park removed, "
            "the first-write-wins envelope made overwritable, the schedule author or the requester's lineage "
            "dropped, objective_start's lineage replaced with the person, COMMAND_EXECUTION taken out of the "
            "unreachable set. 787 tests across the 36 policy-touching files pass. Scope: the memory transform "
            "is MEM-002 (Slice 3); a conversation's own tool calls are the person's by design (TRUST-001)."),
    }),
    ("SEC-005", {
        "current_implementation": (
            "The envelope is a declared contract (friday/contracts.py Envelope, NetworkScope, Limits: every PRD 43 "
            "field plus lineage; narrow-only, tests/test_envelope.py). Every compiled objective is minted one - "
            "its capabilities exactly the plan's, FRIDAY's file roots, public-web read, a 7-day expiry - "
            "recorded on objective_runs and written once. The capability runtime refuses a capability outside "
            "the envelope, or after it expires, before any policy question; a port that cannot carry the Run is "
            "held to the envelope by the executor; a run with no envelope parks. execution.py and contracts.py "
            "are kernel paths; the duplicate sandbox.py is retired. Gap: roots, network, secrets, limits, token "
            "and tool-call limits are recorded but not yet enforced, and no worker launch (CLI, fabric, Hermes) "
            "is compiled from the envelope. HOST stays the default backend by owner decision (2026-09-25)."),
        "files": ["friday/contracts.py:450,535,585", "friday/objectives.py:375", "friday/capability_runtime.py:359",
                  "friday/continuous.py:944,963", "friday/execution.py:138", "friday/fsjail.py:163",
                  "friday/self_upgrade.py:55-60", "tests/test_execution.py:84,128,134,226,284"],
        "tests": ["tests/test_envelope.py::test_nothing_can_be_widened",
                  "tests/test_envelope.py::test_narrowing_can_tighten_every_field",
                  "tests/test_envelope.py::test_the_boundary_a_run_meets_is_kernel",
                  "tests/test_envelope_lineage.py::test_a_capability_outside_the_envelope_is_refused",
                  "tests/test_envelope_lineage.py::test_an_expired_envelope_refuses_every_capability",
                  "tests/test_envelope_lineage.py::test_a_reopened_run_keeps_the_envelope_it_was_compiled_with",
                  "tests/test_execution.py::test_a_path_cannot_climb_out",
                  "tests/test_execution.py::test_deny_all_is_the_default",
                  "tests/test_execution.py::test_a_fork_bomb_hits_the_process_limit_not_the_machine"],
        "next_action": (
            "EXTEND (Slice 2 steps 3-8, docs/plans/prd-v7-truth-audit/04-slice-2-design.md): enforce roots "
            "(fsjail with the envelope's roots), network (NET-001 grants in netguard), tool-call and token "
            "limits; compile the CLI launch, fabric spawn and Hermes bridge from the envelope; test an "
            "out-of-envelope write, host and capability, and an expired envelope, denied per worker type."),
    }),
    # --- Slice 2, step 3: native enforcement, 2026-09-26
    ("SEC-005", {
        "current_implementation": (
            "The envelope is a declared contract (friday/contracts.py Envelope, NetworkScope, Limits: every PRD 43 "
            "field plus lineage; narrow-only). Every compiled objective is minted one and records it once on "
            "objective_runs. Enforced for FRIDAY's own capabilities: capability membership and expiry (by the "
            "executor before every dispatch, whatever the port, and again by capability_runtime); file and "
            "document roots, read vs write, on the resolved path after FRIDAY's own jail (toolsets/files."
            "resolve_for); token and tool-call limits, which only tighten the objective budget the executor and "
            "the model gateway both consult; an expired envelope parks the run for the person. A session "
            "pre-approval cannot lift an envelope block, and a run with no envelope parks. execution.py and "
            "contracts.py are kernel paths; the duplicate sandbox.py is retired. Gap: network scope (NET-001) "
            "and secret aliases are recorded but not enforced, and no worker launch (CLI, fabric, Hermes) is "
            "compiled from the envelope yet. HOST stays the default backend by owner decision (2026-09-25)."),
        "files": ["friday/contracts.py:450,535,585", "friday/objectives.py:375", "friday/capability_runtime.py:359",
                  "friday/continuous.py:944,963", "friday/toolsets/files.py:140,161",
                  "friday/objective_budget.py:144,158", "friday/execution.py:138",
                  "friday/self_upgrade.py:55-60"],
        "tests": ["tests/test_envelope.py::test_nothing_can_be_widened",
                  "tests/test_envelope_lineage.py::test_a_capability_outside_the_envelope_is_refused",
                  "tests/test_envelope_files.py::test_a_read_outside_the_envelope_is_refused",
                  "tests/test_envelope_files.py::test_a_read_root_is_not_a_write_root",
                  "tests/test_envelope_files.py::test_a_move_needs_write_on_both_ends",
                  "tests/test_envelope_limits.py::test_a_token_limit_tightens_the_ceiling",
                  "tests/test_envelope_limits.py::test_a_tool_call_limit_tightens_the_class_limit",
                  "tests/test_envelope_limits.py::test_an_expired_envelope_parks_the_run_for_the_person",
                  "tests/test_envelope_limits.py::test_a_port_that_carries_the_run_is_still_held_to_the_envelope",
                  "tests/test_envelope_limits.py::test_a_session_approval_cannot_lift_an_envelope_block"],
        "evidence": (
            "2026-09-26: 83 envelope tests pass (contract 40, lineage 19, files 9, limits 8, plus schedules). "
            "Mutations turned them red: the file check removed, write roots widened to read roots, a move's "
            "source treated as a read, documents resolving without the run, the token or tool-call tightening "
            "removed, expiry not parking, the executor trusting a run-carrying port, the runtime envelope check "
            "removed. 418 tests across 33 budget, executor and gateway suites and 440 across 24 file suites "
            "pass."),
    }),
    # --- Slice 2, step 5: network scope grants, 2026-09-26
    ("NET-001", {
        "status": VERIFIED,
        "current_implementation": (
            "contracts.NetworkScope carries PRD 44's six modes, the objective, an expiry and a grant id. Every "
            "compiled objective is issued a grant in the store (network_grants), audited on issue and on "
            "revocation (trust.AuditLog, action network.grant), revocable one at a time or all at once, from "
            "any process; a grant the store has never seen counts as revoked. netguard.check holds a run with "
            "an envelope to its scope - an expired scope, a revoked grant and an out-of-scope host are refused, "
            "the last before any name is resolved - and the address rules still apply inside every scope. "
            "Wired through netguard.run_scope into web_fetch, research (both engines) and browser navigation, "
            "which fails closed when the scope cannot be checked. A conversation (no envelope) keeps the "
            "ordinary guard. Found and closed on the way (V7-D14): the opt-in crawl4ai engine handed URLs to "
            "its own browser with no netguard or sensitive-domain check. Scope: a worker process's own egress "
            "is ADVISORY on HOST (owner decision 2026-09-25) and is compiled from the envelope in SEC-005 "
            "step 8."),
        "files": ["friday/contracts.py:535", "friday/netguard.py:194,204,214,233", "friday/store.py:2349,2363,2375",
                  "friday/objectives.py:414,430,441", "friday/toolsets/research.py:138,218",
                  "friday/browser_capability.py:92", "friday/trust.py:122-227"],
        "tests": ["tests/test_network_scope.py::test_a_host_outside_the_scope_is_refused_before_any_lookup",
                  "tests/test_network_scope.py::test_an_expired_scope_is_refused",
                  "tests/test_network_scope.py::test_a_grant_revoked_in_another_process_is_refused_here",
                  "tests/test_network_scope.py::test_an_unknown_grant_counts_as_revoked",
                  "tests/test_network_scope.py::test_compiling_an_objective_issues_an_audited_grant",
                  "tests/test_network_scope.py::test_the_web_toolset_holds_a_durable_run_to_its_scope",
                  "tests/test_network_scope.py::test_the_crawl4ai_engine_is_held_to_the_same_guard",
                  "tests/test_network_scope.py::test_the_browser_loop_passes_the_scope_to_navigation",
                  "tests/test_network_scope.py::test_a_scope_that_cannot_be_checked_blocks_navigation",
                  "tests/test_trust.py::test_out_of_scope_host_and_action_are_blocked_even_when_the_tool_asks"],
        "evidence": (
            "2026-09-26: 16 tests pass without touching the network (out-of-scope hosts are refused before "
            "resolution, which a resolver that raises proves; in-scope hosts are public IP literals). A grant "
            "revoked through one Store connection is refused through another. Ten mutations each turned a test "
            "red: the scope check, revocation, expiry and unknown-grant rules removed; web_fetch and research not "
            "passing the scope; the crawl4ai landing check removed; the browser scope check removed or made to "
            "fail open; grant issue at compile removed. 1,122 tests across 50 network and browser suites pass."),
    }),
    # --- Slice 2, steps 6-7: composition and the emergency stop, 2026-09-26
    ("CAP-002", {
        "status": VERIFIED,
        "current_implementation": (
            "policy.composition_verdict judges a capability set as a whole: private reads (MEMORY_READ, screen, "
            "camera, secrets, and the READ_LOCAL_SAFE tools that read the person's own content) together with an "
            "arbitrary send (web, browser, terminal, the person's desktop) escalate - DENY when the objective "
            "came from read material or nobody is attending, CONFIRM for the person. compile_objective applies "
            "it before anything is persisted, the one door every objective comes through; objective_start asks "
            "through the shared confirmation book and spends the yes on exactly that request and capability "
            "set, and DANGEROUS autonomy is the owner's standing yes, audited as AUTO. Every capability now "
            "meets the policy (V7-D15: seven had no category and ran ungated) and every category has a "
            "composition class. Scope: judged per objective; a conversation's tool calls are judged one at a "
            "time by policy and provenance."),
        "files": ["friday/policy.py:609,637,641,662", "friday/objectives.py:168,181,588",
                  "friday/toolsets/objectives.py:326"],
        "tests": ["tests/test_composition.py::test_private_read_with_arbitrary_send_escalates",
                  "tests/test_composition.py::test_private_read_alone_or_send_alone_is_not_escalated",
                  "tests/test_composition.py::test_the_composition_is_refused_from_read_material_or_unattended",
                  "tests/test_composition.py::test_compiling_an_untrusted_exfiltration_shape_is_refused",
                  "tests/test_composition.py::test_objective_start_asks_and_then_spends_the_yes",
                  "tests/test_composition.py::test_every_capability_meets_the_policy",
                  "tests/test_composition.py::test_every_category_has_a_composition_class"],
        "evidence": (
            "2026-09-26: 12 tests pass: each class alone passes, the pair escalates; read material or unattended "
            "is refused at compile before anything is written; the person is asked, the yes is spent once "
            "(a second start with it is refused), the MCP retry runs as the run that asked, and DANGEROUS "
            "proceeds with an AUTO audit record. Before the change every test failed, including the DANGEROUS "
            "one, which found no audit record: the composition had gone through unjudged."),
    }),
    ("STOP-001", {
        "status": VERIFIED,
        "current_implementation": (
            "friday/stop.py, owned by objective-engine and asking no model. emergency_stop latches one row in "
            "the shared store (stop_latch), revokes every network grant, parks every running objective PAUSED "
            "with the reason, runs this process's hooks and kills the worker processes it owns by PID; a "
            "watcher in every long-running process (server.main, the voice entrypoint) sees the latch and "
            "runs that process's hooks. While latched nothing new starts: the capability runtime, the "
            "objective executor, every MCP tool (ownership.guard) and the text brain's tool door refuse. "
            "Hooks: coding-CLI workers (owned PIDs), fabric providers, the Hermes gateway, workbench "
            "previews, a desktop takeover. Triggers: `launcher --stop` (latches before ending every process, "
            "and ends them even when the store cannot latch), the spoken or typed phrase - matched before any "
            "model hears the turn, interrupting FRIDAY's own speech. Resume is the person's explicit act "
            "(`--resume` or 'resume everything') and the latch survives a restart until then. Scope: a "
            "browser primitive already in flight ends within its own timeout; the latch refuses the next."),
        "files": ["friday/stop.py:51,122,139,165,177", "friday/capability_runtime.py:344",
                  "friday/ownership.py:228", "friday/launcher.py:557", "agent_friday.py:1811",
                  "server.py:32", "friday/objectives.py:485"],
        "tests": ["tests/test_emergency_stop.py::test_a_worker_that_ignores_the_stop_is_killed_by_pid",
                  "tests/test_emergency_stop.py::test_the_voice_turn_stops_before_the_model_and_silences_itself",
                  "tests/test_emergency_stop.py::test_the_text_brain_stops_without_asking_its_model",
                  "tests/test_emergency_stop.py::test_nothing_new_starts_while_latched",
                  "tests/test_emergency_stop.py::test_every_conversational_tool_refuses_while_latched",
                  "tests/test_emergency_stop.py::test_every_process_runs_its_own_hooks_when_another_one_stops",
                  "tests/test_emergency_stop.py::test_stop_latches_revokes_every_grant_and_parks_the_work",
                  "tests/test_emergency_stop.py::test_resume_is_explicit_and_the_latch_survives_a_restart",
                  "tests/test_emergency_stop.py::test_the_terminal_stop_latches_before_it_ends_the_processes"],
        "evidence": (
            "2026-09-26: 17 tests pass. A real OS worker that never stops on its own is killed by PID; the text "
            "brain and the voice turn stop without their model being asked; a stop pressed through one store "
            "connection runs the hooks watching another; the latch survives reopening the store; the terminal "
            "latches before ending processes. Ten mutations each turned a test red: the runtime, executor and "
            "MCP-guard latch checks removed, the PID kill removed, the watcher not running hooks, grant "
            "revocation removed, the text-brain and voice phrase checks removed, the terminal not latching, "
            "the desktop hook not registered."),
    }),
    ("INTENT-001", {
        "files": ["friday/requirements.py:270", "friday/requirements.py:488", "friday/planner_model.py:328",
                  "friday/planner_model.py:350", "friday/objectives.py:367",
                  "tests/test_golden_engineering_journey.py:57-69", "friday/planner.py:116-117"],
        "next_action": (
            "EXTEND: write the missing golden-journey test from free text through requirements.understand + "
            "plan_objective, asserting compile_objective's run/task rows, and confirm the live chat/voice loop "
            "calls requirements.understand() before plan_objective(). Also fix a literal-preservation defect "
            "found 2026-09-26: the deterministic clause splitter (planner.py:116-117) splits on any '.', so a "
            "file name such as notes.txt is cut into two clauses and the plan invents a path and content; a "
            "golden case must pin that a file name, path or quoted text survives planning verbatim."),
    }),
    # --- Slice 2 review fixes and hygiene, 2026-09-26 (04-slice-2-design.md, "Implementation review")
    ("SEC-005", {
        "current_implementation": (
            "The envelope is a declared contract (friday/contracts.py Envelope, NetworkScope, Limits: every PRD 43 "
            "field plus lineage; narrow-only). Every compiled objective is minted one and records it once. A "
            "caller may hand compile a narrower envelope, never a wider one: no capability the plan does not "
            "use, no root, host or secret compile would not grant, no later or missing expiry, and no network "
            "grant compile did not issue (V7-D23). The stored form is read strictly - lineage and expiry must "
            "be there - or the run parks (V7-D24). Enforced for FRIDAY's own capabilities: membership and "
            "expiry (executor and runtime), file roots read vs write, token and tool-call limits, and the "
            "network scope (NET-001). HOST coding workers get the allowlisted environment every backend "
            "shares, never FRIDAY's own keys (V7-D18): the CLI's own configuration names and a launch's named "
            "passthroughs cross, a secret-shaped name never does. Gap: no worker launch is compiled from the "
            "envelope yet (the CLI's tool lists and WebFetch rules, fabric spawn, the Hermes bridge), and "
            "sub-workers do not get child envelopes. HOST stays the default backend by owner decision "
            "(2026-09-25)."),
        "files": ["friday/contracts.py:450,535,657,721", "friday/objectives.py:414,441,541",
                  "friday/capability_runtime.py:333", "friday/continuous.py:629",
                  "friday/toolsets/files.py:140,161", "friday/objective_budget.py:144,158",
                  "friday/execution.py:139,142", "friday/executors/cli.py:71,145,284",
                  "friday/self_upgrade.py:55-60"],
        "tests": ["tests/test_envelope.py::test_nothing_can_be_widened",
                  "tests/test_envelope_lineage.py::test_a_capability_outside_the_envelope_is_refused",
                  "tests/test_envelope_lineage.py::test_a_supplied_envelope_with_no_expiry_is_refused_at_compile",
                  "tests/test_envelope_lineage.py::test_a_supplied_envelope_cannot_grant_what_the_plan_does_not_use",
                  "tests/test_envelope_lineage.py::test_a_supplied_envelope_cannot_bring_its_own_network_grant",
                  "tests/test_envelope_lineage.py::test_a_narrower_supplied_envelope_is_admitted_as_supplied",
                  "tests/test_envelope_lineage.py::test_a_stored_envelope_that_leaves_something_out_is_not_trusted",
                  "tests/test_envelope_files.py::test_a_read_outside_the_envelope_is_refused",
                  "tests/test_envelope_files.py::test_a_read_root_is_not_a_write_root",
                  "tests/test_envelope_limits.py::test_a_token_limit_tightens_the_ceiling",
                  "tests/test_envelope_limits.py::test_an_expired_envelope_parks_the_run_for_the_person",
                  "tests/test_envelope_limits.py::test_a_port_that_carries_the_run_is_still_held_to_the_envelope",
                  "tests/test_envelope_limits.py::test_a_session_approval_cannot_lift_an_envelope_block",
                  "tests/test_worker_environment.py::test_a_host_coding_run_does_not_inherit_friday_secrets",
                  "tests/test_worker_environment.py::test_a_secret_shaped_name_never_crosses_even_when_asked_for"],
        "evidence": (
            "2026-09-26, after the Slice 2 review: compile refuses a supplied envelope with no expiry, one "
            "granting a capability the plan does not use, and one carrying its own grant id, and admits a "
            "narrower one exactly as supplied. A stored envelope with no expiry, a missing provenance or "
            "attended, a non-boolean attended, or a network scope that never expires parks the run with "
            "nothing dispatched. Mutations turned these red: the wider-than check removed, the grant check "
            "removed, admission skipped, the expiry and scope-expiry checks removed, the attended type check "
            "removed, and both provenance layers reverted together (either alone is masked by the other). "
            "Worker environment: a HOST coding run's environment holds neither a known key nor an unlisted "
            "secret-shaped one; mutations (HOST env back to None, the allowlist replaced by the whole "
            "environment, the secret-shape guard removed) each turned a test red. The 156 envelope, contract, "
            "worker-environment and budget tests pass, in one run of the 37 test files touched this session "
            "(574 passed, 1 skipped)."),
        "next_action": (
            "EXTEND (Slice 2 step 8): compile the CLI Launch from the envelope (allowed and disallowed tool "
            "lists; WebFetch domain rules in --settings from the network scope), fabric spawn and the Hermes "
            "bridge; derive sub-worker envelopes with narrow(); test an out-of-envelope write, host and "
            "capability, and an expired envelope, denied per worker type."),
    }),
    ("TRUST-002", {
        "current_implementation": (
            "Lineage is retained through the objective and schedule transforms, and is never assumed. "
            "compile_objective requires the caller's provenance and attendance - there is no default (V7-D25) - "
            "and records them, with a minted execution envelope, on objective_runs (written once). The stored "
            "envelope is read strictly: a row without its lineage or with a non-boolean attended is not "
            "trusted, and the run parks (V7-D24). The executor rebuilds each task's Run from it and "
            "capability_runtime.execute applies provenance_verdict to it. Schedules record their author: a "
            "firing takes the less trusted of author and requester, and only a hand firing is attended. Rows "
            "from before the migration park for the person. Product Envelope constructions state their lineage "
            "(static guard). Command execution is unreachable from read material (V7-D13). Scope: a "
            "conversation's own Run still defaults to the person, attended (V7-D28, open)."),
        "files": ["friday/objectives.py:414,441,541", "friday/contracts.py:721",
                  "friday/continuous.py:104,629", "friday/capability_runtime.py:333",
                  "friday/store.py:1002,1007,2265,2594", "friday/toolsets/schedules.py:353,397,536,572",
                  "friday/toolsets/objectives.py:403", "friday/policy.py:225,228"],
        "tests": ["tests/test_envelope_lineage.py::test_read_material_is_refused_a_destructive_capability_on_the_durable_path",
                  "tests/test_envelope_lineage.py::test_a_read_material_objective_cannot_delegate_to_a_terminal",
                  "tests/test_envelope_lineage.py::test_no_command_execution_tool_is_reachable_from_read_material",
                  "tests/test_envelope_lineage.py::test_the_persons_objective_reaches_the_ordinary_question",
                  "tests/test_envelope_lineage.py::test_a_schedule_written_from_read_material_fires_as_read_material",
                  "tests/test_envelope_lineage.py::test_running_a_schedule_by_hand_passes_the_requesters_lineage",
                  "tests/test_envelope_lineage.py::test_a_run_from_before_envelopes_waits_for_the_person",
                  "tests/test_envelope_lineage.py::test_a_stored_envelope_that_leaves_something_out_is_not_trusted",
                  "tests/test_envelope_lineage.py::test_compile_will_not_assume_the_lineage",
                  "tests/test_envelope_lineage.py::test_every_product_caller_states_the_lineage",
                  "tests/test_trust.py::test_injected_page_content_cannot_reach_destructive_tools"],
        "evidence": (
            "2026-09-26: as before (a READ_MATERIAL objective driven through capability_runtime.build_dispatch() "
            "under DANGEROUS autonomy is refused power_shutdown and hermes_delegate; schedules keep their "
            "author), plus the review fixes: compile raises TypeError when either keyword is left out (restoring "
            "the defaults turns it red), and a stored envelope whose provenance was dropped no longer reads as "
            "the person. Making the keywords required surfaced two subprocess scripts inside tests "
            "(tests/test_invariants.py, tests/test_chaos_restart.py) that had relied on the default where no "
            "static check could see them; 44 test calls in 21 files now state their lineage. One run of the 37 "
            "test files touched this session: 574 passed, 1 skipped."),
    }),
    ("STOP-001", {
        "tests": ["tests/test_emergency_stop.py::test_a_worker_that_ignores_the_stop_is_killed_by_pid",
                  "tests/test_emergency_stop.py::test_the_voice_turn_stops_before_the_model_and_silences_itself",
                  "tests/test_emergency_stop.py::test_the_text_brain_stops_without_asking_its_model",
                  "tests/test_emergency_stop.py::test_nothing_new_starts_while_latched",
                  "tests/test_emergency_stop.py::test_every_conversational_tool_refuses_while_latched",
                  "tests/test_emergency_stop.py::test_every_process_runs_its_own_hooks_when_another_one_stops",
                  "tests/test_emergency_stop.py::test_stop_latches_revokes_every_grant_and_parks_the_work",
                  "tests/test_emergency_stop.py::test_resume_is_explicit_and_the_latch_survives_a_restart",
                  "tests/test_emergency_stop.py::test_the_terminal_stop_latches_before_it_ends_the_processes",
                  "tests/test_emergency_stop.py::test_work_waiting_on_a_worker_is_parked_without_polling_it",
                  "tests/test_emergency_stop.py::test_no_repair_runs_once_the_stop_is_latched"],
        "files": ["friday/stop.py:51,122,139,165,177", "friday/capability_runtime.py:333",
                  "friday/continuous.py:629,909", "friday/ownership.py:228", "friday/launcher.py:557",
                  "agent_friday.py:1811", "server.py:32", "friday/objectives.py:519"],
        "evidence": (
            "2026-09-26: 19 tests pass. A real OS worker that never stops on its own is killed by PID; the text "
            "brain and the voice turn stop without their model being asked; a stop pressed through one store "
            "connection runs the hooks watching another; the latch survives reopening the store; the terminal "
            "latches before ending processes. After the Slice 2 review (V7-D26): work already handed to a "
            "worker is parked without polling it, and a stop pressed while a poll is in flight drives no "
            "repair - before the fix the executor polled three times while latched and then ran a "
            "RECONCILE_THEN_RESTART repair against the gateway the stop had killed. Twelve mutations each "
            "turned a test red: the runtime, executor and MCP-guard latch checks removed, the PID kill removed, "
            "the watcher not running hooks, grant revocation removed, the text-brain and voice phrase checks "
            "removed, the terminal not latching, the desktop hook not registered, the latch check before the "
            "worker poll disabled, the no-repair check removed."),
    }),
    ("CAP-002", {
        "current_implementation": (
            "policy.composition_verdict judges a capability set as a whole: private reads (MEMORY_READ, screen, "
            "camera, secrets, and the READ_LOCAL_SAFE tools that read the person's own content) together with an "
            "arbitrary send (web, browser, terminal, the person's desktop) escalate - DENY when the objective "
            "came from read material or nobody is attending, CONFIRM for the person. A capability no policy "
            "category covers cannot be judged and counts as able to both read and send (V7-D27). "
            "compile_objective applies it before anything is persisted; objective_start asks through the shared "
            "confirmation book and spends the yes on exactly that request and capability set, and DANGEROUS "
            "autonomy is the owner's standing yes, audited as AUTO. At runtime an uncategorised capability meets "
            "the engine's unknown-tool rule (ASK, under any autonomy) instead of no decision. Every capability "
            "in the registry has a category (V7-D15) and every category a composition class. Scope: judged per "
            "objective; a conversation's tool calls are judged one at a time by policy and provenance."),
        "files": ["friday/policy.py:609,637,641,662", "friday/objectives.py:181,541",
                  "friday/capability_runtime.py:413", "friday/toolsets/objectives.py:326"],
        "tests": ["tests/test_composition.py::test_private_read_with_arbitrary_send_escalates",
                  "tests/test_composition.py::test_private_read_alone_or_send_alone_is_not_escalated",
                  "tests/test_composition.py::test_the_composition_is_refused_from_read_material_or_unattended",
                  "tests/test_composition.py::test_compiling_an_untrusted_exfiltration_shape_is_refused",
                  "tests/test_composition.py::test_objective_start_asks_and_then_spends_the_yes",
                  "tests/test_composition.py::test_every_capability_meets_the_policy",
                  "tests/test_composition.py::test_every_category_has_a_composition_class",
                  "tests/test_composition.py::test_a_capability_no_category_covers_is_asked_not_run",
                  "tests/test_composition.py::test_a_capability_no_category_covers_counts_as_reading_and_sending"],
        "evidence": (
            "2026-09-26: 14 tests pass: each class alone passes, the pair escalates; read material or unattended "
            "is refused at compile before anything is written; the person is asked, the yes is spent once, and "
            "DANGEROUS proceeds with an AUTO audit record. After the Slice 2 review (V7-D27): with its categories "
            "removed, a planted toolset function that does not gate itself was run on no decision before the "
            "fix and is now refused as APPROVAL_REQUIRED under DANGEROUS, and the uncategorised capability is "
            "DENY for read material and CONFIRM for the person. Three mutations (the runtime skipping unknown "
            "tools, composition dropping unknown reads, composition_of dropping unknown capabilities) each "
            "turned a test red. 980 tests across the 44 runtime- and policy-touching files pass."),
    }),
    ("NET-001", {
        "files": ["friday/contracts.py:535", "friday/netguard.py:194,204,214,233", "friday/store.py:2349,2363,2375",
                  "friday/objectives.py:414,441", "friday/toolsets/research.py:138,218",
                  "friday/browser_capability.py:92,122", "friday/trust.py:122-227"],
        "tests": ["tests/test_network_scope.py::test_a_host_outside_the_scope_is_refused_before_any_lookup",
                  "tests/test_network_scope.py::test_an_expired_scope_is_refused",
                  "tests/test_network_scope.py::test_a_grant_revoked_in_another_process_is_refused_here",
                  "tests/test_network_scope.py::test_an_unknown_grant_counts_as_revoked",
                  "tests/test_network_scope.py::test_compiling_an_objective_issues_an_audited_grant",
                  "tests/test_network_scope.py::test_the_web_toolset_holds_a_durable_run_to_its_scope",
                  "tests/test_network_scope.py::test_the_crawl4ai_engine_is_held_to_the_same_guard",
                  "tests/test_network_scope.py::test_the_browser_loop_passes_the_scope_to_navigation",
                  "tests/test_network_scope.py::test_a_scope_that_cannot_be_checked_blocks_navigation",
                  "tests/test_browser_capability.py::test_a_url_the_address_rules_cannot_judge_is_blocked",
                  "tests/test_envelope_lineage.py::test_a_supplied_envelope_cannot_bring_its_own_network_grant",
                  "tests/test_trust.py::test_out_of_scope_host_and_action_are_blocked_even_when_the_tool_asks"],
        "evidence": (
            "2026-09-26: 16 scope tests pass without touching the network (out-of-scope hosts are refused before "
            "resolution; in-scope hosts are public IP literals), and a grant revoked through one Store "
            "connection is refused through another; ten mutations each turned a test red. After the Slice 2 "
            "review: the browser's address rules, which the scope sits on, now fail closed (V7-D22) - three "
            "malformed loopback URLs were ALLOWED before - and compile refuses an envelope that brings its own "
            "grant id, so a grant is only ever one compile issued (V7-D23). A stored scope that reaches "
            "anywhere must expire (V7-D24). 67 browser and network tests pass."),
    }),
    # --- The live agent's objective port (V7-D30) and the suite runner (V7-D31), 2026-09-27
    ("TRUST-002", {
        "files": ["friday/objectives.py:414,441,541", "friday/contracts.py:721",
                  "friday/continuous.py:104,629", "friday/capability_runtime.py:333",
                  "friday/store.py:1002,1007,2265,2594", "friday/toolsets/schedules.py:353,397,536,572",
                  "friday/toolsets/objectives.py:403", "friday/policy.py:225,228", "agent_friday.py:1538"],
        "tests": ["tests/test_envelope_lineage.py::test_read_material_is_refused_a_destructive_capability_on_the_durable_path",
                  "tests/test_envelope_lineage.py::test_a_read_material_objective_cannot_delegate_to_a_terminal",
                  "tests/test_envelope_lineage.py::test_a_port_that_cannot_carry_the_run_still_meets_the_lineage",
                  "tests/test_envelope_lineage.py::test_the_live_agents_objective_port_carries_the_run",
                  "tests/test_envelope_lineage.py::test_no_command_execution_tool_is_reachable_from_read_material",
                  "tests/test_envelope_lineage.py::test_the_persons_objective_reaches_the_ordinary_question",
                  "tests/test_envelope_lineage.py::test_a_schedule_written_from_read_material_fires_as_read_material",
                  "tests/test_envelope_lineage.py::test_running_a_schedule_by_hand_passes_the_requesters_lineage",
                  "tests/test_envelope_lineage.py::test_a_run_from_before_envelopes_waits_for_the_person",
                  "tests/test_envelope_lineage.py::test_a_stored_envelope_that_leaves_something_out_is_not_trusted",
                  "tests/test_envelope_lineage.py::test_compile_will_not_assume_the_lineage",
                  "tests/test_envelope_lineage.py::test_every_product_caller_states_the_lineage",
                  "tests/test_trust.py::test_injected_page_content_cannot_reach_destructive_tools"],
        "evidence": (
            "2026-09-27, adding to the entries above: the live agent's objective engine called a port that "
            "took no run, so on that path a READ_MATERIAL objective reached command execution as the person "
            "(V7-D30). The executor now asks capability_runtime.lineage_refusal before any port is called, "
            "and the agent's port carries the task's Run. Both tests failed first (the executor test with "
            "'read material reached a terminal as [PERSON]'); a mutation of each layer turns its test red. "
            "Across the 28 files that import agent_friday or drive the objective engine: 579 passed, 1 failed; "
            "the failure was a test stand-in without the real port's run parameter, corrected, and that "
            "file then passed (10)."),
    }),
    # --- Slice 2 step 8a: the coding CLI compiled from the envelope, 2026-09-27
    ("SEC-005", {
        "current_implementation": (
            "The envelope is a declared contract (friday/contracts.py Envelope, NetworkScope, Limits: every PRD 43 "
            "field plus lineage; narrow-only). Every compiled objective is minted one and records it once. A "
            "caller may hand compile a narrower envelope, never a wider one (V7-D23); the stored form is read "
            "strictly or the run parks (V7-D24). Enforced for FRIDAY's own capabilities: membership and expiry "
            "(executor and runtime), file roots read vs write, token and tool-call limits, and the network "
            "scope (NET-001). HOST coding workers get the allowlisted environment, never FRIDAY's own keys "
            "(V7-D18). The Claude Code launch is now compiled from an envelope (brokers.within_envelope): "
            "dontAsk, so a call no rule allows is refused by the CLI instead of waiting on a prompt; the "
            "envelope's roots become the CLI's working directories (--add-dir) with reads outside them "
            "blocked; no bare file-tool rule (one matches every path), edits only as Edit(//root/**) under "
            "write roots; WebFetch only inside the network scope (domain rules for hosted modes, none for NONE, "
            "an expired scope or SECURITY_TARGETS); no shell for READ_MATERIAL; nothing the task itself says is "
            "read. The executor refuses an expired or out-of-envelope launch before admission, ends the run at "
            "the envelope's expiry and stops it at the first tool call past the limit, and the launch reports "
            "how strongly each part holds (file roots and network ADVISORY on the host while a shell command "
            "may run). Gap: no product path hands the Claude Code executor an envelope yet - the durable path "
            "reaches coding work through hermes_delegate as an MCP call with no Run - and fabric spawn, the "
            "Hermes bridge and sub-worker child envelopes are not compiled from it. HOST stays the default "
            "backend by owner decision (2026-09-25)."),
        "files": ["friday/contracts.py:460,554,604,636,643,647,662,745", "friday/objectives.py:414,441,541",
                  "friday/capability_runtime.py:333,370", "friday/continuous.py:104,662",
                  "friday/toolsets/files.py:140,161", "friday/objective_budget.py:144,158",
                  "friday/execution.py:139,142", "friday/executors/cli.py:71,148,188",
                  "friday/executors/brokers.py:151,178,197", "friday/executors/claude_code.py:263,319,325,362",
                  "friday/self_upgrade.py:55-60"],
        "tests": ["tests/test_envelope.py::test_nothing_can_be_widened",
                  "tests/test_envelope_lineage.py::test_a_capability_outside_the_envelope_is_refused",
                  "tests/test_envelope_lineage.py::test_a_supplied_envelope_with_no_expiry_is_refused_at_compile",
                  "tests/test_envelope_lineage.py::test_a_supplied_envelope_cannot_grant_what_the_plan_does_not_use",
                  "tests/test_envelope_lineage.py::test_a_supplied_envelope_cannot_bring_its_own_network_grant",
                  "tests/test_envelope_lineage.py::test_a_narrower_supplied_envelope_is_admitted_as_supplied",
                  "tests/test_envelope_lineage.py::test_a_stored_envelope_that_leaves_something_out_is_not_trusted",
                  "tests/test_envelope_files.py::test_a_read_outside_the_envelope_is_refused",
                  "tests/test_envelope_files.py::test_a_read_root_is_not_a_write_root",
                  "tests/test_envelope_limits.py::test_a_token_limit_tightens_the_ceiling",
                  "tests/test_envelope_limits.py::test_an_expired_envelope_parks_the_run_for_the_person",
                  "tests/test_envelope_limits.py::test_a_port_that_carries_the_run_is_still_held_to_the_envelope",
                  "tests/test_envelope_limits.py::test_a_session_approval_cannot_lift_an_envelope_block",
                  "tests/test_worker_environment.py::test_a_host_coding_run_does_not_inherit_friday_secrets",
                  "tests/test_worker_environment.py::test_a_secret_shaped_name_never_crosses_even_when_asked_for",
                  "tests/test_envelope_workers.py::test_without_an_envelope_the_launch_is_what_it_was",
                  "tests/test_envelope_workers.py::test_an_expired_envelope_launches_nothing",
                  "tests/test_envelope_workers.py::test_the_workspace_must_lie_inside_the_envelope",
                  "tests/test_envelope_workers.py::test_a_writing_run_needs_its_workspace_in_a_write_root",
                  "tests/test_envelope_workers.py::test_anything_the_envelope_does_not_allow_is_denied_not_asked",
                  "tests/test_envelope_workers.py::test_file_tools_are_held_to_the_roots_not_opened_everywhere",
                  "tests/test_envelope_workers.py::test_an_exploring_run_under_an_envelope_has_no_file_writers",
                  "tests/test_envelope_workers.py::test_a_task_cannot_widen_the_roots_it_was_given",
                  "tests/test_envelope_workers.py::test_web_tools_follow_the_network_scope",
                  "tests/test_envelope_workers.py::test_read_material_gets_no_shell",
                  "tests/test_envelope_workers.py::test_the_run_ends_when_its_envelope_does",
                  "tests/test_envelope_workers.py::test_the_run_is_stopped_past_its_tool_call_limit",
                  "tests/test_envelope_workers.py::test_how_strongly_each_part_holds_is_reported"],
        "evidence": (
            "2026-09-27, Slice 2 step 8a: the 17 tests in tests/test_envelope_workers.py drive the real "
            "launch_for and execute and read the real argv (only the CLI process is faked); all 17 failed first "
            "(no envelope parameter). Rule syntax is from the CLI's permissions documentation (// absolute "
            "paths, Windows drives as /c/, WebFetch(domain:*.x) for subdomains, deny before allow, a bare "
            "tool-name rule matching everywhere) and dontAsk is among the installed CLI's modes (2.1.283). "
            "Thirteen mutations each turned a test red: dontAsk removed, bare file readers kept, the "
            "READ_MATERIAL shell strip removed, hosted scopes given bare web tools, SECURITY_TARGETS treated as "
            "hosted, scope expiry ignored, the envelope expiry, workspace and write-root refusals removed, the "
            "tool-call stop removed, the expiry clamp removed, explore's write denial removed, and file roots "
            "reported ENFORCED under a shell. 278 contract, envelope and executor tests pass together. The CLI "
            "was not launched under these flags in this step: that the CLI honours them rests on its "
            "documentation, and a live launch belongs to the end-to-end journey."),
        "next_action": (
            "EXTEND (Slice 2 step 8b-d): give the durable path a coding capability that carries the Run "
            "(hermes_delegate resolved in the runtime, so the envelope reaches the Hermes bridge: cwd, "
            "constraints, environment, expiry), compile fabric spawn from the envelope (env limited to its "
            "secrets, cwd, expiry, refused when expired), derive sub-worker envelopes with narrow(), and run "
            "one live Claude Code launch under a test envelope to confirm the CLI refuses an out-of-root edit "
            "and an out-of-scope fetch."),
    }),
    # --- Slice 2 step 8b-d and the step 8 review, 2026-09-27
    ("SEC-005", {
        "current_implementation": (
            "The envelope is a declared contract (friday/contracts.py Envelope, NetworkScope, Limits: every PRD 43 "
            "field plus lineage; narrow-only), minted once per compiled objective, admitted only narrower (V7-D23) "
            "and read strictly (V7-D24). Each worker type is held to it, with the strength reported per part "
            "(contracts.ENFORCED / ADVISORY): FRIDAY-native capabilities (membership, expiry, file roots, "
            "network scope, token and tool-call limits, all ENFORCED); the Claude Code CLI, compiled by "
            "brokers.within_envelope (dontAsk; the roots as working directories with reads outside blocked; "
            "edits only as Edit rules under write roots, under both spellings of a linked root; WebFetch only "
            "inside the network scope; no shell for READ_MATERIAL; network paths refused; an isolated run's "
            "worktree must lie in a write root; the executor refuses before admission, ends the run at expiry "
            "and stops it past the tool-call limit); Hermes, reached from the durable path by router fallback, "
            "named its objective by the agent's port (RUN_BOUND_TOOLS) and held by the server to that "
            "objective's stored envelope (hermes_bridge.bounded_by: session cwd, roots and scope in the task, "
            "refusal; expiry kept on the work run and enforced by timer and by the digest's overdue sweep; the "
            "id accepted only while that objective is running a delegation); sub-workers get a child envelope "
            "from narrow() (DevelopmentRun), and the Hermes team path, which cannot carry one, is not taken "
            "under an envelope. The fabric's sidecars are shared processes and no toolset reaches the fabric; a "
            "tripwire test names one that starts to. On HOST (the owner's default, 2026-09-25) a shell command "
            "a worker may run and a worker's own network are ADVISORY, and are reported so."),
        "files": ["friday/contracts.py:460,502,506,565,615,647,654,658,673,756",
                  "friday/objectives.py:414,441,541", "friday/capability_runtime.py:333,370",
                  "friday/continuous.py:104,662", "friday/toolsets/files.py:140,161",
                  "friday/objective_budget.py:144,158", "friday/execution.py:139,142",
                  "friday/executors/cli.py:80,154,194", "friday/executors/brokers.py:153,176,190,203",
                  "friday/executors/claude_code.py:263,287,333,339,376", "friday/hermes_bridge.py:471,617,627,1946,1965",
                  "friday/tools/hermes_control.py:101,140", "agent_friday.py:1542,1573",
                  "friday/development.py:259", "friday/progress_digest.py:241",
                  "friday/self_upgrade.py:55-60"],
        "tests": ["tests/test_envelope.py::test_nothing_can_be_widened",
                  "tests/test_envelope_lineage.py::test_a_capability_outside_the_envelope_is_refused",
                  "tests/test_envelope_lineage.py::test_a_supplied_envelope_with_no_expiry_is_refused_at_compile",
                  "tests/test_envelope_lineage.py::test_a_stored_envelope_that_leaves_something_out_is_not_trusted",
                  "tests/test_envelope_files.py::test_a_read_outside_the_envelope_is_refused",
                  "tests/test_envelope_files.py::test_a_read_root_is_not_a_write_root",
                  "tests/test_envelope_limits.py::test_an_expired_envelope_parks_the_run_for_the_person",
                  "tests/test_envelope_limits.py::test_a_port_that_carries_the_run_is_still_held_to_the_envelope",
                  "tests/test_worker_environment.py::test_a_host_coding_run_does_not_inherit_friday_secrets",
                  "tests/test_envelope_workers.py::test_an_expired_envelope_launches_nothing",
                  "tests/test_envelope_workers.py::test_the_workspace_must_lie_inside_the_envelope",
                  "tests/test_envelope_workers.py::test_a_writing_run_needs_its_workspace_in_a_write_root",
                  "tests/test_envelope_workers.py::test_anything_the_envelope_does_not_allow_is_denied_not_asked",
                  "tests/test_envelope_workers.py::test_file_tools_are_held_to_the_roots_not_opened_everywhere",
                  "tests/test_envelope_workers.py::test_a_task_cannot_widen_the_roots_it_was_given",
                  "tests/test_envelope_workers.py::test_web_tools_follow_the_network_scope",
                  "tests/test_envelope_workers.py::test_read_material_gets_no_shell",
                  "tests/test_envelope_workers.py::test_the_run_ends_when_its_envelope_does",
                  "tests/test_envelope_workers.py::test_the_run_is_stopped_past_its_tool_call_limit",
                  "tests/test_envelope_workers.py::test_how_strongly_each_part_holds_is_reported",
                  "tests/test_envelope_workers.py::test_no_capability_reaches_the_fabric_without_carrying_its_envelope",
                  "tests/test_envelope_workers.py::test_a_sub_worker_gets_a_child_envelope_no_wider_than_its_parent",
                  "tests/test_envelope_workers.py::test_a_sub_worker_cannot_be_given_more_than_its_parent",
                  "tests/test_envelope_workers.py::test_a_network_path_root_is_refused_before_it_is_touched",
                  "tests/test_envelope_workers.py::test_a_linked_root_is_allowed_under_both_its_spellings",
                  "tests/test_envelope_workers.py::test_an_isolated_run_needs_its_worktree_inside_a_write_root",
                  "tests/test_envelope_hermes.py::test_a_delegation_is_compiled_from_its_envelope",
                  "tests/test_envelope_hermes.py::test_an_envelope_that_does_not_allow_it_delegates_nothing",
                  "tests/test_envelope_hermes.py::test_an_objectives_delegation_is_held_to_its_stored_envelope",
                  "tests/test_envelope_hermes.py::test_an_objective_with_no_readable_envelope_is_refused",
                  "tests/test_envelope_hermes.py::test_work_cannot_be_charged_to_an_objective_that_is_not_delegating",
                  "tests/test_envelope_hermes.py::test_the_objective_port_names_its_objective_to_a_run_bound_tool",
                  "tests/test_envelope_hermes.py::test_a_model_supplied_objective_id_is_replaced_not_trusted",
                  "tests/test_envelope_hermes.py::test_a_hermes_task_is_interrupted_when_its_envelope_expires",
                  "tests/test_envelope_hermes.py::test_the_expiry_is_kept_on_the_work_run_and_holds_without_its_timer",
                  "tests/test_envelope_hermes.py::test_a_finished_task_leaves_no_expiry_timer"],
        "evidence": (
            "2026-09-27, Slice 2 step 8 complete. tests/test_envelope_workers.py (24) and "
            "tests/test_envelope_hermes.py (13) drive the real launch_for/execute, the real bounded_by, the MCP "
            "tool and the agent's port, and a fake Hermes gateway for the expiry interrupt; each failed first. "
            "32 mutations, one per guard, each turned a test red (13 for the CLI compile, 8 for the Hermes path, "
            "4 for child envelopes and the fabric tripwire, 7 for the review fixes); one was not run on "
            "purpose: removing the network-path refusal would make "
            "the code resolve a UNC path, which can contact that host. friday-final-reviewer: SHIP-WITH-FIXES, "
            "nothing widening authority; all five findings fixed (04-slice-2-design.md, 'Step 8 implementation "
            "review'). Live, 2026-09-27, the installed CLI (2.1.283) launched by the executor under a test envelope "
            "(write root = workspace, no network): a Write inside the root landed; a Write outside it was refused "
            "by the CLI ('Permission to use Write has been denied because Claude Code is running in don't ask "
            "mode'); WebFetch was absent (the model searched for it and found nothing). That run first exposed "
            "V7-D35 (every executor run on this machine died on the CLI's first line), fixed. "
            "The four-chunk suite over this tree (data/baseline_v7_slice3b): chunks 0-2 exit 0 (1,293, 1,011 and "
            "1,311 passed); chunk 3 exit 1 with 959 passed and one failure, the soak harness building a "
            "Candidate without the now-required provenance, fixed and tests/test_soak.py re-run green (the "
            "candidate scan now covers scripts/ too)."),
        "next_action": (
            "EXTEND: Hermes' file roots and network are ADVISORY beyond the exec bridge's jail, which is the "
            "Hermes profile's workspace rather than the task's envelope; bind the bridge's jail to the "
            "delegation's write roots per session (or record the owner's acceptance of that limit under HOST). "
            "Then judge this row."),
    }),
    # --- Slice 3: memory trust (MEM-002, MEM-003, V7-D34), 2026-09-27
    ("MEM-002", {
        "current_implementation": (
            "Every memory written through the promotion gate says where it came from: Candidate.provenance is "
            "required (contracts.PERSON or READ_MATERIAL) and stored as memories.trust_class (additive migration; "
            "'' for older rows and for writers that do not classify). Read material may be remembered but never "
            "with the person's voice: it cannot set a preference, record a decision (a decision settles a coding "
            "worker's questions), or be filed under the scopes FRIDAY is shown as what the person wants and who "
            "they are; it cannot overrule a row the person wrote (or an older unclassified one) whatever "
            "confidence it claims, the disagreement being flagged; and what the person says replaces read "
            "material. Producers state their lineage: worker handoffs and skill files READ_MATERIAL, the "
            "profile-system second pass PERSON. The brain ledger, whose facts FRIDAY is given as rules, records "
            "who wrote each entry, decided by the caller (never by the provenance string), and refuses a worker "
            "or read-material origin: the Hermes exec bridge routes a worker's fact through the gate as read "
            "material (V7-D34). Older rows from those producers' code-set sources are marked read as the "
            "column arrives; a page-resolved YouTube channel is written as read; a read candidate is checked "
            "against every row it would retire; and a ledger entry that does not say who wrote it is not a "
            "rule (fail-closed; the person restores one by marking it). Residual, stated: a conversation's own "
            "memory tools write unclassified rows whatever the model read that turn, because the "
            "conversation's Run lineage is still open (V7-D28)."),
        "files": ["friday/memory_promotion.py:34,50,97", "friday/store.py:164,1140,1410", "friday/brain.py:94,254",
                  "friday/tools/execution_bridge.py:212", "friday/tools/brain_control.py:75",
                  "friday/toolsets/brain.py:66", "friday/memory_stack.py:152",
                  "friday/autolearn.py:199", "friday/skills/bridge.py:94"],
        "tests": ["tests/test_memory_trust.py::test_read_material_cannot_set_a_preference",
                  "tests/test_memory_trust.py::test_read_material_cannot_record_a_decision",
                  "tests/test_memory_trust.py::test_read_material_cannot_overrule_what_the_person_said",
                  "tests/test_memory_trust.py::test_the_person_overrules_read_material_whatever_it_claimed",
                  "tests/test_memory_trust.py::test_read_material_lands_marked_as_read",
                  "tests/test_memory_trust.py::test_what_the_person_said_lands_as_theirs",
                  "tests/test_memory_trust.py::test_a_candidate_must_say_where_it_came_from",
                  "tests/test_memory_trust.py::test_every_product_candidate_states_where_it_came_from",
                  "tests/test_memory_trust.py::test_a_worker_cannot_plant_a_rule_by_naming_a_file",
                  "tests/test_memory_trust.py::test_a_ledger_entry_records_who_wrote_it",
                  "tests/test_memory_trust.py::test_the_adapter_refuses_a_worker_write_whatever_it_calls_itself",
                  "tests/test_memory_trust.py::test_the_rules_tier_never_renders_a_worker_written_entry",
                  "tests/test_memory_poisoning.py::test_read_material_cannot_speak_for_the_person",
                  "tests/test_memory_trust_legacy.py::test_rows_the_read_producers_wrote_before_are_marked_read",
                  "tests/test_memory_trust_legacy.py::test_a_page_resolved_channel_is_remembered_as_read",
                  "tests/test_memory_trust_legacy.py::test_read_material_cannot_retire_an_older_row_the_person_wrote",
                  "tests/test_memory_trust_legacy.py::test_a_ledger_entry_that_does_not_say_who_wrote_it_is_not_a_rule"],
        "evidence": (
            "2026-09-27: the 13 memory-trust tests and the 4 scope tests failed first; before the fix a worker's "
            "bridge_brain_remember('...', provenance='docs/policy.md:1') was inserted into the ledger, which the "
            "rules tier returns every turn (V7-D34). 12 mutations, one per guard, each turned a test red "
            "(preference, decision and scope refusals, the overrule guard, the person-wins rule, the stored "
            "class, the required field, the adapter refusal, the ledger origin, the rules-tier skip, the "
            "bridge's label). friday-final-reviewer: SHIP-WITH-FIXES, no guard broken; its four findings are "
            "dispositioned (03-checkpoint.md, Slice 3), three fixed with four failing-first tests and four more "
            "mutations, the fourth being V7-D28. Existing tests were given the lineage they represent, none "
            "loosened. "
            "The four-chunk suite over this tree (data/baseline_v7_slice3b): chunks 0-2 exit 0 (1,293, 1,011 and "
            "1,311 passed); chunk 3 exit 1 with 959 passed and one failure, the soak harness building a "
            "Candidate without the now-required provenance, fixed and tests/test_soak.py re-run green (the "
            "candidate scan now covers scripts/ too)."),
        "next_action": (
            "EXTEND with TRUST-001/V7-D28: classify the conversation's own memory writes by what the turn read "
            "(a turn that consumed a tool result or page writes READ_MATERIAL), then this row can be judged."),
    }),
    ("MEM-003", {
        "current_implementation": (
            "A poisoning lifecycle is tested as a whole: hostile content arrives as read material and is "
            "remembered marked as read; aged ninety days it stays read material; it cannot settle a coding "
            "worker's question (QuestionBroker answers only from what the person stated); the person forgets it "
            "and states what is true, and the same content arriving again with higher confidence cannot "
            "overrule them. A worker cannot plant a rule in the brain ledger (V7-D34). Residual as MEM-002: "
            "conversation-written memories are unclassified (V7-D28)."),
        "files": ["friday/memory_promotion.py:97", "friday/executors/brokers.py:458", "friday/store.py:1656",
                  "friday/brain.py:254", "friday/memory_stack.py:152"],
        "tests": ["tests/test_memory_poisoning.py::test_a_read_fact_cannot_drive_a_later_action_and_is_repaired",
                  "tests/test_memory_poisoning.py::test_read_material_cannot_speak_for_the_person",
                  "tests/test_memory_trust.py::test_a_worker_cannot_plant_a_rule_by_naming_a_file",
                  "tests/test_invariants.py::test_memory_read_material_cannot_write_a_rule_directly"],
        "evidence": (
            "2026-09-27: before the fix, a page fact promoted by the gate answered QuestionBroker's 'which push "
            "target should I use?' with source='fact' and the attacker's remote. The lifecycle test failed first; "
            "removing the broker's read-material skip turns it red again. "
            "The four-chunk suite over this tree (data/baseline_v7_slice3b): chunks 0-2 exit 0 (1,293, 1,011 and "
            "1,311 passed); chunk 3 exit 1 with 959 passed and one failure, the soak harness building a "
            "Candidate without the now-required provenance, fixed and tests/test_soak.py re-run green (the "
            "candidate scan now covers scripts/ too)."),
        "next_action": (
            "EXTEND with MEM-002's residual (conversation writes, V7-D28); a delayed-execution case through the "
            "voice brain's own memory tools belongs there."),
    }),
    # --- Slice 4: INTENT-001, the objective contract and literal preservation, 2026-09-27
    ("INTENT-001", {
        "current_implementation": (
            "The intent compiler now reads what the person said into an objective contract that travels with "
            "the run (contracts.ObjectiveContract, objective_runs.contract, written once at compile): the goal, "
            "why they want it (a purpose stated before an instruction is not a step), what must not happen and "
            "how the work must be done, how to speak, what done means per goal, and the exact names and words "
            "they gave. Those literals reach the plan verbatim: a full stop ends a sentence only before a space, "
            "quoted words are content and never split or read as instructions, a named file or path and the "
            "words given for it are used as given (FRIDAY names its own file only when none was named, and "
            "invents no words for one that was), and compile refuses a plan that dropped a literal "
            "(LiteralDropped) before anything is written. A prohibition is kept as a safety rule, never planned "
            "as what it forbids (V7-D39). objective_start (and the live agent, through it) compiles with the "
            "contract. Gaps: the contract's assumptions and open questions are not yet filled from "
            "requirements.understand / still_blocking, and on a turn that is both an idea and a command the "
            "live loop plans before it raises open questions (V7-D38)."),
        "files": ["friday/planner.py:119,142,148,153,157,168,201,426,897,942",
                  "friday/contracts.py:796", "friday/objectives.py:168,187,652",
                  "friday/store.py:1064", "friday/toolsets/objectives.py:435",
                  "friday/requirements.py:270"],
        "tests": ["tests/test_intent_journey.py::test_a_file_name_is_not_the_end_of_a_sentence",
                  "tests/test_intent_journey.py::test_quoted_words_are_never_split",
                  "tests/test_intent_journey.py::test_a_named_file_and_its_content_reach_the_plan_verbatim",
                  "tests/test_intent_journey.py::test_quoted_content_is_written_word_for_word",
                  "tests/test_intent_journey.py::test_with_no_name_given_friday_names_its_own_file",
                  "tests/test_intent_journey.py::test_a_named_file_without_content_gets_none_invented",
                  "tests/test_intent_journey.py::test_a_noun_after_an_article_is_not_a_second_command",
                  "tests/test_intent_journey.py::test_words_inside_quotes_do_not_steer_the_plan",
                  "tests/test_intent_journey.py::test_a_purpose_stated_before_an_instruction_is_not_a_step",
                  "tests/test_intent_journey.py::test_said_alone_a_purpose_is_still_the_request",
                  "tests/test_intent_journey.py::test_an_idea_becomes_an_objective_that_can_run",
                  "tests/test_intent_journey.py::test_the_idea_runs_and_the_file_holds_the_persons_words",
                  "tests/test_intent_journey.py::test_a_prohibition_is_never_planned_as_the_thing_it_forbids",
                  "tests/test_intent_journey.py::test_listed_content_is_kept_whole",
                  "tests/test_objective_contract.py::test_the_compiler_extracts_the_contract",
                  "tests/test_objective_contract.py::test_the_contract_round_trips",
                  "tests/test_objective_contract.py::test_compile_keeps_the_contract_with_the_run",
                  "tests/test_objective_contract.py::test_a_plan_that_dropped_what_the_person_named_is_not_compiled",
                  "tests/test_objective_contract.py::test_starting_an_objective_from_words_keeps_its_contract"],
        "evidence": (
            "2026-09-27: before, 'create notes.txt containing hello world' planned files_create with path "
            "'friday-notes.txt' and content 'Created by Friday for: create notes', and 'create notes.txt and do "
            "not delete anything' planned automations_delete. End to end now: 'I want to keep a reading list: "
            "create reading.md containing Dune' is planned, compiled with its contract and driven by the "
            "objective executor through the real capability runtime to COMPLETED, and reading.md holds 'Dune' "
            "(tests/test_intent_journey.py::test_the_idea_runs_and_the_file_holds_the_persons_words; reverting "
            "the named-path or the purpose rule turns it red). The new tests failed first; each rule has a "
            "mutation that turns a test red (17 in all, two of which first survived and were pinned by sharper "
            "tests). The 62 planner, objective and golden test files pass together (1,035 passed, after an "
            "order-dependence in the new journey test - an inherited file-jail singleton - was fixed). "
            "The four-chunk suite over this tree (data/baseline_v7_slice4a): all four chunks exit 0, 4,597 "
            "passed (1,293, 991, 1,268 and 1,045), none failed."),
        "next_action": (
            "EXTEND: fill the contract's assumptions and open questions from requirements.understand and "
            "still_blocking at compile, and fix V7-D38 (admission waits on blocking questions); then judge."),
    }),
    # --- Slice 4: TRAJ-001, the run-level trajectory check, 2026-09-27
    ("TRAJ-001", {
        "current_implementation": (
            "A run-level trajectory check (friday/trajectory.py) reads a run as a whole from its own recorded "
            "events since the person last resumed it, its task rows and the largest fraction of any budget "
            "spent, and returns ON_TRACK, NO_PROGRESS or DRIFTING with reasons: attempts failing across "
            "several tasks with nothing succeeding; one failure kind recurring across tasks (a blip that hit "
            "every running task once is not drift); policy refusals piling up; spend far ahead of progress "
            "below the ceilings (a spent budget stays the budget's to stop, with its exact reason); a "
            "write or external-action step started again and again. The executor asks before dispatching "
            "ready tasks, and pauses a flagged run with the reasons as its blocker, a trajectory.flagged event "
            "and one message to the person (carry on, change the plan, or cancel); resuming starts a new "
            "window. It reads the newest events (Store.recent_objective_events), not the oldest thousand. "
            "Gaps against PRD §12: objective alignment and scope creep against the objective contract, user "
            "amendments, unverified high-consequence assumptions and worker conflict are not yet signals, and "
            "the only action is pause-and-ask (no fresh worker, stronger model or lower concurrency)."),
        "files": ["friday/trajectory.py:30,34,39,42,44,48,78,142,157", "friday/continuous.py:523,879",
                  "friday/store.py:3212", "friday/objective_budget.py:158"],
        "tests": ["tests/test_trajectory.py::test_a_side_effecting_step_that_finally_succeeded_is_not_flagged",
                  "tests/test_trajectory.py::test_a_healthy_run_is_never_flagged",
                  "tests/test_trajectory.py::test_concurrent_transient_blips_rarely_ask_the_person",
                  "tests/test_trajectory.py::test_no_progress_across_tasks_is_flagged_within_its_threshold",
                  "tests/test_trajectory.py::test_one_task_stuck_alone_is_left_to_the_task_level_detector",
                  "tests/test_trajectory.py::test_one_cause_failing_every_task_is_flagged_once_it_recurs",
                  "tests/test_trajectory.py::test_repeated_policy_refusals_are_flagged",
                  "tests/test_trajectory.py::test_spend_far_ahead_of_progress_is_flagged",
                  "tests/test_trajectory.py::test_a_budget_already_spent_is_left_to_the_budget",
                  "tests/test_trajectory.py::test_a_side_effecting_step_run_again_and_again_is_flagged",
                  "tests/test_trajectory.py::test_a_resume_starts_a_new_window",
                  "tests/test_trajectory.py::test_a_run_failing_across_tasks_is_paused_with_its_reasons",
                  "tests/test_trajectory.py::test_a_healthy_run_completes_unflagged"],
        "evidence": (
            "2026-09-27, the synthetic long-horizon drift benchmark (tests/test_trajectory.py): 40 seeded "
            "sequential runs of 60 tasks with recovering failures are never flagged; under a concurrent model "
            "(rounds of four tasks, each attempt failing with p=0.3, success by the third attempt) 3 of 200 "
            "healthy runs are paused (1.5%, bound 2%); no progress is flagged at exactly the 6th failed attempt "
            "after onset, a recurring cause at the third task's second failure, 3 refusals, 75% spend at 10% "
            "done, and a side-effecting step's third start. A real ContinuousTaskExecutor run whose every call "
            "times out across 4 tasks is paused with its reasons (without the hook it runs its tasks to "
            "permanent failure), and a healthy run completes unflagged. Each signal, the window reset, the "
            "executor hook and both thresholds have a mutation that turns a test red (11). The 37 "
            "objective-engine test files pass (497), after the burn signal was kept below the budget's own "
            "ceiling (it had pre-empted test_a_run_past_its_wall_clock_budget_is_paused). "
            "The four-chunk suite over this tree (data/baseline_v7_slice4b): all four chunks exit 0, 4,649 "
            "passed (1,315, 1,011, 1,336 and 987), none failed. "
            "The independent review of 2026-09-27 found a side-effecting step that succeeded after retries still paused the run afterwards (V7-D47). Fixed: only a step not yet succeeded counts (friday/trajectory.py:114); the new test failed before. "
            "The four-chunk suite over the fixed tree (data/baseline_v7_slice4g): all four chunks exit 0, 4,684 passed (1,259, 1,042, 1,286 and 1,097), none failed."),
        "next_action": (
            "EXTEND: objective alignment and scope creep against the objective contract (INTENT-001), "
            "amendment and assumption signals, and the non-pause actions of §12."),
    }),
    # --- Amendment A2: APPROVAL-001 and JIT-001, 2026-09-27
    ("APPROVAL-001", {
        "current_implementation": (
            "A person's yes to a pending objective action is a lease on the plan it was given for: "
            "resume_after_approval records it with the objective's plan hash (each task's capability, "
            "arguments and dependencies, without the approval marker; objectives.plan_hash), its graph "
            "version (modification.graph_version) and an expiry (objectives.APPROVAL_TTL, 24 hours). The "
            "runtime honours it (capability_runtime._approval_on_record) only for the exact capability and "
            "arguments, while unexpired, and while the plan hash is unchanged: a task added or an argument "
            "amended afterwards, an expired yes, or a record from before leases (no plan hash) no longer "
            "authorises, and the run asks again. A standing permission stays a policy rule, not an approval. "
            "Gap: blast radius is not in the binding, because SIDE-002's blast-radius estimate does not exist "
            "yet."),
        "files": ["friday/objectives.py:443,446", "friday/continuous.py:1607",
                  "friday/capability_runtime.py:456,497", "friday/store.py:2953",
                  "friday/modification.py:84"],
        "tests": ["tests/test_approval_lease.py::test_an_approval_on_one_objective_does_not_authorise_another",
                  "tests/test_approval_lease.py::test_a_call_outside_any_objective_cannot_use_an_objective_approval",
                  "tests/test_approval_lease.py::test_an_approval_for_the_same_plan_is_honoured",
                  "tests/test_approval_lease.py::test_the_approval_records_the_plan_it_was_given_for",
                  "tests/test_approval_lease.py::test_a_material_change_to_the_plan_makes_the_approval_stale",
                  "tests/test_approval_lease.py::test_an_expired_approval_does_not_authorise",
                  "tests/test_approval_lease.py::test_an_approval_from_before_leases_is_not_honoured"],
        "evidence": (
            "2026-09-27: driven through the real executor and capability runtime under GUARDED autonomy: "
            "before, an approval given before a task was appended, an expired one and one with no plan all "
            "still let files_create run; after, each parks the run WAITING_PERMISSION with no file written, "
            "and an approval for the unchanged plan completes the run and writes the file. Five mutations "
            "(the plan check, the expiry check, a missing plan tolerated, the plan not recorded, the approval "
            "marker hashed) each turn a test red. 48 approval, runtime and executor test files pass (771). "
            "The four-chunk suite over this tree (data/baseline_v7_slice4c): all four chunks exit 0, 4,660 "
            "passed (1,298, 998, 1,316 and 1,048), none failed. "
            "The independent review of 2026-09-27 found an approval on one objective authorised another objective, and a call outside any objective, that wrote the marker into its own arguments (V7-D43, high): the lookup scanned every run. Fixed: the approval is read only from the executing objective's run (friday/capability_runtime.py:405,457), and a call with no envelope gets none; both new tests failed before. The golden bench had been dropping the Run on its way to the runtime, so it never exercised this check (V7-D48); it now carries it, and all 164 golden cases pass. "
            "The four-chunk suite over the fixed tree (data/baseline_v7_slice4g): all four chunks exit 0, 4,684 passed (1,259, 1,042, 1,286 and 1,097), none failed."),
        "next_action": "EXTEND with SIDE-002: add the blast-radius estimate to the binding once it exists.",
    }),
    ("JIT-001", {
        "current_implementation": (
            "Authority is checked again at the moment of action. Each compiled objective's envelope records "
            "what every planned capability was when the plan was made (capability_runtime.fingerprint: its "
            "side effect, execution scope, edge requirement and policy questions, plus the source of the "
            "function it resolves to, read without importing it), and the runtime compares it before every "
            "call on the run: a capability changed since is refused 'JIT: ... has changed since this "
            "objective was planned'. A narrower envelope cannot swap a version, and the stored form keeps them. "
            "At execution the runtime already re-reads the STOP latch, the envelope's membership and expiry, "
            "the lineage verdict and the policy decision; netguard re-checks the network grant; approvals are "
            "leases on the plan (APPROVAL-001). Gaps: authentication validity and target-state compatibility "
            "are not re-checked generically, and envelopes from before fingerprints carry none."),
        "files": ["friday/capability_runtime.py:380,524,554", "friday/contracts.py:639,731",
                  "friday/objectives.py:492"],
        "tests": ["tests/test_jit_authority.py::test_a_narrower_envelope_cannot_drop_a_capability_version",
                  "tests/test_jit_authority.py::test_compile_records_the_versions_on_an_envelope_it_is_handed",
                  "tests/test_jit_authority.py::test_the_plan_records_what_each_capability_was",
                  "tests/test_jit_authority.py::test_an_unchanged_capability_runs",
                  "tests/test_jit_authority.py::test_a_capability_changed_since_the_plan_is_refused_at_execution",
                  "tests/test_jit_authority.py::test_the_fingerprint_follows_what_the_capability_is_registered_to_do",
                  "tests/test_jit_authority.py::test_the_fingerprint_follows_the_code_it_runs",
                  "tests/test_jit_authority.py::test_a_narrower_envelope_cannot_swap_a_capability_version",
                  "tests/test_jit_authority.py::test_a_stored_envelope_keeps_the_versions"],
        "evidence": (
            "2026-09-27: the five new guard tests failed first; a capability planned against another version "
            "is refused NOT_PERMITTED at execution while the same capability unchanged runs. Six mutations "
            "each turn a test red (the execution check, recording at compile, the narrow check, the stored "
            "form, and each half of the fingerprint); the stored-form test first survived its mutation and "
            "was rebuilt from envelope_for. 52 envelope, runtime and objective test files pass (793). "
            "The four-chunk suite over this tree (data/baseline_v7_slice4c): all four chunks exit 0, 4,660 "
            "passed (1,298, 998, 1,316 and 1,048), none failed. "
            "The independent review of 2026-09-27 found a narrower envelope could drop the versions its parent pinned and so switch this check off (V7-D44, high). Fixed: every pinned version must be kept unchanged (friday/contracts.py:739), and compile writes its own versions onto an envelope a caller hands it while still refusing one that pins a different version; both new tests failed before. "
            "The four-chunk suite over the fixed tree (data/baseline_v7_slice4g): all four chunks exit 0, 4,684 passed (1,259, 1,042, 1,286 and 1,097), none failed."),
        "next_action": (
            "EXTEND: re-check authentication and target state before consequential actions where a "
            "capability can say what it needs."),
    }),
    # --- Amendment A2: BCON-001, behavioural contracts enforced, 2026-09-27
    ("BCON-001", {
        "current_implementation": (
            "The objective contract (INTENT-001) now carries governance that is enforced, not only recorded "
            "(planner.governance_for): an action class the person forbade outright ('do not delete "
            "anything', 'never send it'), a named thing protected from one ('don't delete config.yaml'), and "
            "a ceiling on changes ('at most 20 changes'). Action classes (delete, send, spend, power) are "
            "one shared vocabulary for requests and capability ids (contracts.ACTION_CLASSES). Compile "
            "refuses a plan that does what was forbidden, whoever wrote it (objectives.ForbiddenByContract), "
            "before anything is written; the ceiling travels in the envelope (max_side_effects, narrow-only) "
            "and the executor refuses the change after the Nth before any port sees it. The execution "
            "envelope already enforces allowed and blocked capabilities, roots, network, limits and expiry. "
            "Gaps: data invariants (e.g. currency, id matching) and preconditions are not expressed or "
            "checked, a prohibition about something unnamed and specific ('don't touch my photos') stays a "
            "recorded constraint, and declared recovery (resume_failed_items_only) belongs to OBJ-004's "
            "per-item state, not built."),
        "files": ["friday/contracts.py:643,813,821,852", "friday/planner.py:943,947,954",
                  "friday/objectives.py:176,184,716,741", "friday/continuous.py:1043"],
        "tests": ["tests/test_behaviour_contract.py::test_a_change_still_in_flight_counts_toward_the_limit",
                  "tests/test_behaviour_contract.py::test_an_edit_to_the_plan_is_held_to_the_same_contract",
                  "tests/test_behaviour_contract.py::test_the_contract_reads_what_is_forbidden_and_what_is_limited",
                  "tests/test_behaviour_contract.py::test_a_named_prohibition_protects_what_it_names",
                  "tests/test_behaviour_contract.py::test_a_plan_that_does_what_the_person_forbade_is_not_compiled",
                  "tests/test_behaviour_contract.py::test_a_protected_name_cannot_be_the_target_of_what_was_forbidden",
                  "tests/test_behaviour_contract.py::test_the_change_after_the_limit_is_refused_not_made"],
        "evidence": (
            "2026-09-27: the five tests failed first. Driven through the real executor and capability "
            "runtime, an objective of three file creations with 'at most 2 changes' writes exactly two files "
            "and the third task is refused 'the person allowed at most 2 changes'; a files_recycle plan under "
            "'do not delete anything' and a files_delete of config.yaml under 'don't delete config.yaml' are "
            "not compiled, while deleting another file is. Seven mutations (the forbidden gate, the "
            "protected check, the executor ceiling, the ceiling put in the envelope, the limit extraction, "
            "the protected extraction, the stored ceiling) each turn a test red. 67 planner, objective, "
            "envelope and contract test files pass (1,134). "
            "The four-chunk suite over this tree (data/baseline_v7_slice4d): all four chunks exit 0, 4,667 "
            "passed (1,303, 1,036, 1,317 and 1,011), none failed. "
            "The independent review of 2026-09-27 found a change still running on a Hermes worker did not count toward the ceiling (V7-D45) and a plan edited after compile was not held to the contract (V7-D46, latent: only skip is wired to voice). Fixed: work waiting on its worker counts (friday/continuous.py:1071), and amend and append run the compile-time check against the stored contract (friday/modification.py:136); both new tests failed before. "
            "The four-chunk suite over the fixed tree (data/baseline_v7_slice4g): all four chunks exit 0, 4,684 passed (1,259, 1,042, 1,286 and 1,097), none failed."),
        "next_action": (
            "EXTEND: data invariants and preconditions a contract can state and the runtime check before and "
            "after each action; recovery rules with OBJ-004's per-item state."),
    }),
    # --- Amendment A2: DUR-001, side effects survive an executor's death, 2026-09-27
    ("DUR-001", {
        "current_implementation": (
            "Every significant state change of an objective is an append-only event (objective_events): "
            "run.created and its classification, task.started, task.succeeded / failed / retry / blocked, "
            "graph edits with their version (modification.py), run.paused / resumed, watchdog.orphaned for a "
            "dead executor, trajectory.flagged, and now side_effect.committed - the first thing written once a "
            "side-effecting call has returned, carrying its result - and task.recovered. When an executor "
            "dies after a side effect and before recording the task's success, the watchdog completes the "
            "task from the commit record instead of re-running it, so the side effect happens once (V7-D42); a "
            "call that did not commit is re-run as before. run_tasks attempts are unique per idempotency key "
            "and terminal deliveries are exactly-once (objective_deliveries). Gaps: the instant between a call "
            "returning and its commit record still re-runs on death (exactly-once there needs the "
            "capability's own idempotency key, not passed yet); events use the engine's own names rather than "
            "D§1's vocabulary; and there is no replay that rebuilds an objective purely from its events "
            "(the task rows remain the state, the events their history)."),
        "files": ["friday/objectives.py:159,161", "friday/continuous.py:285,834,1837",
                  "friday/store.py:879,3203", "friday/modification.py:84"],
        "tests": ["tests/test_durable_side_effects.py::test_a_committed_side_effect_is_not_done_again_after_a_crash",
                  "tests/test_durable_side_effects.py::test_a_call_that_did_not_commit_is_run_again",
                  "tests/test_chaos_restart.py::test_control_plane_killed_mid_task_resumes_in_a_fresh_process"],
        "evidence": (
            "2026-09-27: before, an executor dying after files_create returned and before its success was "
            "recorded led the watchdog to re-drive the task: the side effect ran twice and the run reported "
            "COMPLETED. After, it runs once, the task is SUCCEEDED from its commit (task.recovered) and the "
            "run completes; a call that died mid-flight is run again. Removing the commit record or the "
            "recovery turns the first test red. The 39 objective-engine test files, including the slow "
            "chaos-restart tests that kill real processes, pass (496). "
            "The four-chunk suite over this tree (data/baseline_v7_slice4e): all four chunks exit 0, 4,669 "
            "passed (1,281, 1,022, 1,318 and 1,048), none failed."),
        "next_action": (
            "EXTEND: pass an attempt-scoped idempotency key to side-effecting capabilities that can honour "
            "one, and rebuild an objective's state from its events on replay."),
    }),
    # --- CTX-003: compaction keeps the critical facts or is refused, 2026-09-27
    ("CTX-003", {
        "status": PARTIAL,
        "current_implementation": (
            "friday/compaction.py reads an objective's critical fact set from what the engine recorded, one "
            "verbatim line per fact, in section 19.8's nine kinds: the goal and its purpose (contract, or the "
            "request without one), the exact literals, hard constraints and enforced governance, approvals "
            "given, open questions, succeeded tasks as evidence pointers, failed tasks as approaches to avoid, "
            "the next open task, and the envelope's authority. validate() refuses a packet that lacks any line "
            "(spacing is not content; an exact literal must appear exactly), naming what was lost. An "
            "objective's delegation to a fresh Hermes worker carries the facts in its task bundle - what the "
            "person gave right after the goal, what tools and the plan recorded in a RECORDED STATE section, "
            "every line quoted as data (V7-D49) - and is checked before it is sent (LOSSY_COMPACTION). The voice continuation "
            "(continuity_livekit), which resumes a run in a fresh portion, is built from the run's facts - the "
            "whole objective, the task, the evidence so far, the authority - and checked before the claim is "
            "acted on. Both packets are built from the facts they are checked against, so the check cannot "
            "fire today: it guards against a later change to either builder, which is what the negative-"
            "control tests simulate. Gaps: the Claude Code development packet neither carries nor checks the fact set; the "
            "checkpoint summary is still cut at 2,000 characters (it holds no critical fact); nothing in FRIDAY "
            "yet writes "
            "a model summary that replaces history, so the validator guards packets rather than a "
            "summarise-then-check loop; decisions come from approvals only, not plan amendments."),
        "files": ["friday/compaction.py:45,54,90,149,161,171", "friday/tools/hermes_control.py:192-207",
                  "friday/hermes_bridge.py:269,365", "friday/continuity_livekit.py:273,384",
                  "tests/negative_controls.py:64"],
        "tests": ["tests/test_compaction.py::test_what_tools_recorded_is_kept_apart_from_what_the_person_gave",
                  "tests/test_compaction.py::test_a_step_line_keeps_its_arguments_bounded",
                  "tests/test_envelope_hermes.py::test_tool_output_reaches_the_worker_only_as_quoted_data",
                  "tests/test_envelope_hermes.py::test_an_objective_from_read_material_gives_the_worker_nothing_binding",
                  "tests/test_compaction.py::test_every_kind_of_critical_fact_is_read_from_the_record",
                  "tests/test_compaction.py::test_a_packet_that_carries_every_fact_is_accepted",
                  "tests/test_compaction.py::test_dropping_any_one_fact_is_rejected",
                  "tests/test_compaction.py::test_a_literal_in_other_words_is_not_kept",
                  "tests/test_compaction.py::test_a_literal_keeps_its_own_spacing",
                  "tests/test_compaction.py::test_a_run_without_a_contract_still_keeps_its_request",
                  "tests/test_envelope_hermes.py::test_an_objectives_delegation_carries_its_critical_facts",
                  "tests/test_envelope_hermes.py::test_a_delegation_that_would_drop_a_critical_fact_is_not_sent",
                  "tests/test_continuity_livekit.py::test_the_continuation_packet_carries_every_critical_fact",
                  "tests/test_continuity_livekit.py::test_a_continuation_that_would_drop_a_critical_fact_is_not_sent"],
        "evidence": (
            "2026-09-27: the lossy-summary mutation, run once per fact over a real objective with every kind "
            "present (14 lines), is refused each time with the dropped line named; a literal in other words, "
            "or with its own spacing changed, is refused. Seven mutations of the validator and the fact reader "
            "each turn a test red (the two that first survived sharpened the tests: a literal's own spacing, a "
            "packet reflowed across lines); three mutations of the delegation (facts not added, the check "
            "skipped, the section not rendered) each turn one red. Registered as the lossy-compaction negative "
            "control, the release gate's negative-control check reads PASS, 12/12 (VERIFY-002). "
            "The four-chunk suite over the fixed tree (data/baseline_v7_slice4g): all four chunks exit 0, 4,684 passed (1,259, 1,042, 1,286 and 1,097), none failed. "
            "Later on 2026-09-27 the voice continuation was wired: its two tests failed first; four mutations (the check removed, the check moved after the claim is taken, the old packet restored, the objective cut to 200 characters) each turn one red, and the 39 continuity, voice, Hermes, envelope, golden and ledger test files pass (583). The four-chunk suite above predates this wiring. "
            "The four-chunk suite over this tree (data/baseline_v7_slice5a): all four chunks exit 0, 4,693 passed (1,262, 1,042, 1,286 and 1,103), none failed. "
            "The independent review of Slices 5 and 6 found the first wiring put a failed task's tool output and unbounded arguments among the facts after the goal, labelled to be kept word for word (V7-D49, medium, prompt injection): fixed by splitting what the person gave from what tools recorded, quoting the latter, and capping arguments; the four new tests failed before, and mutations of the split, the provenance rule and the cap each turn one red. The review also found the check cannot fire with today's builders, now stated above. "
            "The four-chunk suite over the tree with the Slice 6 work and the review fixes (data/baseline_v7_slice6b): all four chunks exit 0, 4,707 passed (1,273, 1,059, 1,355 and 1,020), none failed."),
        "next_action": (
            "EXTEND: carry and check the fact set in the Claude Code development packet, and record plan "
            "amendments as decisions."),
    }),
    # --- CTX-002: progressive tool disclosure, measured, 2026-09-27
    ("CTX-002", {
        "status": PARTIAL,
        "current_implementation": (
            "Tools are disclosed in three levels (capability_router, section 19.3): L0 is the group "
            "catalogue, one line per group, in the prompt from the start; L1 is a search result, what a "
            "capability does and the arguments it takes; L2 is a tool's full schema, held only for the core "
            "tools. A long-tail tool is reached through one search and called by name with the arguments L1 "
            "gave, so its L2 never enters the context. disclosure_report measures each request's schema "
            "characters (the starting surface plus the search result as the model receives it) against every "
            "tool's full schema, and whether the one search found what was needed; "
            "scripts/bench_tool_disclosure.py runs it over the full catalogue on two request sets. Gaps: one "
            "search finds the needed capability in its top six for 75.9% of the development split's long-tail "
            "requests, so a quarter need a second search or fail; the disclosure is for tools only, not skills, "
            "memory or documents; nothing measures task success against an all-tools model run."),
        "files": ["friday/capability_router.py:300,346,390,407,576,590", "scripts/bench_tool_disclosure.py",
                  "data/prd_v7_audit/ctx002_tool_disclosure_2026-09-27.json"],
        "tests": ["tests/test_capability_router.py::test_the_active_surface_is_a_fraction_of_the_whole",
                  "tests/test_capability_router.py::test_every_long_tail_request_stays_within_the_catalogue_target"],
        "evidence": (
            "2026-09-27, the full registered catalogue (190 tools, 105,164 schema characters): the starting "
            "surface is 83.9% smaller than showing every tool; every long-tail request costs at least 80% less "
            "- the worst 81.7% over the 123 intent examples and 81.5% over the development split's 965 "
            "long-tail requests - which meets section 82's 80% target. One search finds the needed capability "
            "for 123/123 intent examples (a regression set the router is tuned on, not evidence of "
            "generalisation) and 732/965 development requests (75.9%); the locked holdout was not read. The "
            "starting-surface test now holds section 82's bound (it allowed 55% before). Four mutations turn a "
            "test red: every tool made core, search returning the wrong results, the search result left out "
            "of a request's cost, the baseline inflated (the last two after the test was sharpened to check "
            "its own measure). "
            "The four-chunk suite over this tree (data/baseline_v7_slice5a): all four chunks exit 0, 4,693 passed (1,262, 1,042, 1,286 and 1,103), none failed."),
        "next_action": (
            "EXTEND (owner's target, Amendment A5): benchmark lexical (BM25), embedding, hybrid, metadata "
            "filters, a small local reranker and a skill/tool reranker on Recall@1/@3/@5, schema tokens and "
            "latency, and reach Recall@1 >= 90% and Recall@3 >= 97% on the development split while keeping the "
            "80% disclosure reduction (then confirm once on the holdout); apply L0/L1/L2 to skills, memory and "
            "documents."),
    }),
    # --- CTX-001: the working-set compiler, 2026-09-27
    ("CTX-001", {
        "status": PARTIAL,
        "current_implementation": (
            "model_gateway.compile_working_set compiles one model call's context in section 19.2's order - "
            "system, the objective's record (pinned), the task, current state, memory, code and resources, "
            "tool schemas, history - each with section 19.4's priority, and reports what the package holds "
            "section by section (tokens per section, what was dropped, the total, the budget). Given a budget, "
            "it drops whole sections lowest priority first (history, then tools, code and memory, then "
            "state) and refuses (ContextOverBudget) when the system line, the objective's record and the task "
            "alone do not fit, rather than cut them. What binds the call (the objective and the person's "
            "constraints) and what tools and the plan recorded (current state, data) travel in separate "
            "blocks of the user message, never the system role, every line quoted so nothing inside can "
            "close its block (V7-D50). An objective's model "
            "step (model.infer) now carries the objective's critical facts - none binding when the objective "
            "came from read material - and returns the telemetry with its "
            "result; a call outside any objective is unchanged. Gaps: no caller sets a budget yet (no model "
            "context profile supplies one), so nothing is dropped in production; no production caller passes "
            "state, memory, code or tools to it; the voice turn and the Hermes task bundle assemble their own "
            "context outside it; the telemetry is returned with the result, not stored with the gateway's "
            "call row."),
        "files": ["friday/model_gateway.py:1392,1422,1426,1435,1455", "friday/toolsets/model_gateway.py:50,76,99"],
        "tests": ["tests/test_working_set.py::test_a_recorded_line_cannot_close_its_frame",
                  "tests/test_working_set.py::test_a_model_step_of_an_objective_from_read_material_has_nothing_binding",
                  "tests/test_working_set.py::test_the_sections_come_in_order_and_report_what_they_hold",
                  "tests/test_working_set.py::test_over_budget_the_least_needed_goes_first",
                  "tests/test_working_set.py::test_the_constraints_and_the_task_are_never_dropped",
                  "tests/test_working_set.py::test_an_unbudgeted_plain_call_is_what_compile_context_gives",
                  "tests/test_working_set.py::test_an_objectives_model_step_carries_its_facts_and_reports_its_context",
                  "tests/test_working_set.py::test_a_model_call_outside_any_objective_is_unchanged"],
        "evidence": (
            "2026-09-27: five of the six tests failed before (the sixth pins the unchanged conversation path). "
            "An objective's model step now reaches the gateway with every critical fact of the objective and "
            "reports its sections. Six mutations each turn a test red: P2 dropped before P3, the P0 floor "
            "removed, the objective's record left out of the message, the facts not looked up, the telemetry "
            "emptied, and the record moved into the system role. The 12 gateway, provider, manifest, "
            "objective-MCP and compaction test files pass (184). "
            "The four-chunk suite over this tree (data/baseline_v7_slice5a): all four chunks exit 0, 4,693 passed (1,262, 1,042, 1,286 and 1,103), none failed. "
            "The independent review of Slices 5 and 6 found a bracket inside a fact could close the record's frame (V7-D50): every block now quotes its lines, the recorded state is its own block, and the two new tests failed before; mutations of the quoting, the split and the provenance rule each turn one red. "
            "The four-chunk suite over the tree with the Slice 6 work and the review fixes (data/baseline_v7_slice6b): all four chunks exit 0, 4,707 passed (1,273, 1,059, 1,355 and 1,020), none failed."),
        "next_action": (
            "EXTEND: give each model route a context profile that sets the budget, record the telemetry with "
            "the gateway's call row, and bring the Hermes task bundle and the voice turn onto the compiler."),
    }),
    # --- RES-001: the low-RAM stress test on real memory, 2026-09-27
    ("RES-001", {
        "status": PARTIAL,
        "current_implementation": (
            "The resource governor (friday/governor.py) samples RAM, CPU, disk and browser processes and "
            "admits, queues or sheds new work by pressure level: ELEVATED sheds optional work, HIGH holds "
            "concurrency to one worker, CRITICAL starts nothing new. scripts/stress_low_ram.py drives real "
            "memory pressure: a child process allocates and touches memory in steps while the governor "
            "samples the machine with its own sampler, the RAM thresholds set a few points above the load at "
            "the start so the levels are crossed with at most 1.2 GB (never leaving less than 1.5 GB "
            "available), and it checks every decision against that policy before and after the memory is "
            "released. Gaps: GPU, network and local-model residency (MODEL-005) are not sampled or governed; "
            "work already running is not reduced under pressure, only new work refused or queued; section "
            "55's owned-process record (owner, objective, lease, heartbeat, deadline, cleanup) is not "
            "checked here. On this machine the default thresholds put an idle system at ELEVATED (RAM about "
            "76% against the 75% default), so optional work is shed at rest. The owner decided (Amendment A4) "
            "not to tune the thresholds by hand: RES-003 judges pressure against the machine's own baseline."),
        "files": ["friday/governor.py:59,102,146,261", "scripts/stress_low_ram.py",
                  "data/prd_v7_audit/res001_low_ram_2026-09-27.json"],
        "tests": ["tests/test_governor.py::test_real_memory_pressure_bounds_new_work",
                  "tests/test_governor.py::test_high_pressure_reduces_concurrency_to_one",
                  "tests/test_governor.py::test_critical_pressure_sheds_all_new_work"],
        "evidence": (
            "2026-09-27, real memory on this 15.4 GB host (data/prd_v7_audit/res001_low_ram_2026-09-27.json): "
            "from 76.0% RAM, 300 MB allocated moved the governor to ELEVATED (optional work shed, both workers "
            "admitted), 600 MB to HIGH (first worker admitted, second queued), 750 MB to CRITICAL (nothing "
            "admitted); once released it was NORMAL and admitted all three again. Every decision matched the "
            "policy. The test that runs this is marked slow (it allocates up to 1.2 GB), so the standard gate "
            "leaves it out; it passed when run on its own, and two mutations of the governor (HIGH admitting a "
            "second worker, CRITICAL not shedding) each turn it red. "
            "The four-chunk suite over the tree with the Slice 6 work and the review fixes (data/baseline_v7_slice6b): all four chunks exit 0, 4,707 passed (1,273, 1,059, 1,355 and 1,020), none failed."),
        "next_action": (
            "EXTEND: sample GPU and network, govern local-model residency with MODEL-005, reduce running "
            "optional work under pressure, and record section 55's owned-process fields for every spawned "
            "process."),
    }),
    # --- BROW-001: the per-step action-path order, 2026-09-27
    ("BROW-001", {
        "status": PARTIAL,
        "current_implementation": (
            "friday/action_path.py is section 48's router: each capability acts through a path - native API "
            "(connectors, web), official scripting (apps), a structured tool or MCP (FRIDAY's own tools, "
            "capability_use), the DOM (browser), semantic vision (vision, screen_point) or screen coordinates "
            "(a desktop takeover, which finds its target by vision and clicks at absolute coordinates) - and "
            "rank() puts a step's candidates most direct path first, moving a path that has kept failing (five "
            "tries or more, under half succeeding) behind the others. Capability search now offers tied "
            "matches in that order, so the model tries the more direct path first; which capabilities are "
            "offered is still the score's decision alone (the development-split recall is unchanged, "
            "732/965). The structured path (connector plane, web) and the GUI path (browser, vision, desktop) "
            "both exist as capabilities an objective can mix, and scripts/journey_hybrid_browser.py runs one "
            "objective that does both for real: it reads a public page over HTTP, then opens, reads and "
            "closes the same page in a headless Chromium, through the real executor and capability runtime, "
            "recording what the browser costs the machine. Playwright is now the optional browser extra "
            "(V7-D53). Gaps: nothing yet feeds measured reliability into rank(); the planner does not choose "
            "between alternative paths for a step; the running product's own venv still lacks Playwright."),
        "files": ["friday/action_path.py:25,29,45,58,66", "friday/capability_router.py:543",
                  "scripts/journey_hybrid_browser.py", "pyproject.toml:57",
                  "friday/connectors/plane.py:85-100", "friday/browser_capability.py:37-135"],
        "tests": ["tests/test_action_path.py::test_one_objective_runs_a_structured_step_and_a_real_browser_step",
                  "tests/test_action_path.py::test_the_order_is_section_48s",
                  "tests/test_action_path.py::test_each_path_is_what_the_capability_does",
                  "tests/test_action_path.py::test_every_screen_and_browser_capability_is_on_a_gui_path",
                  "tests/test_action_path.py::test_a_step_takes_the_most_direct_path_first",
                  "tests/test_action_path.py::test_a_path_that_keeps_failing_moves_behind_the_others",
                  "tests/test_capability_router.py::test_a_tie_in_search_goes_to_the_more_direct_path",
                  "tests/test_capability_router.py::test_the_path_order_never_changes_what_is_offered"],
        "evidence": (
            "2026-09-27: the action-path tests and the tie test failed first. The first version of the "
            "tie-break also reordered the whole candidate list, which pushed four needed capabilities out of "
            "the top six on the development split (732 to 728); it was narrowed to order only what is offered, "
            "and the guard test pins that. Five mutations each turn a test red: in the router, the whole list "
            "reordered and the order dropped from what is offered; in action_path, failures ignored, desktop "
            "classed as structured and a single failure demoting a path. The 24 routing, semantics, planner, "
            "capability and golden-suite test files pass (539). "
            "The four-chunk suite over the tree with the Slice 6 work and the review fixes (data/baseline_v7_slice6b): all four chunks exit 0, 4,707 passed (1,273, 1,059, 1,355 and 1,020), none failed. "
            "2026-09-28, REFERENCE-A (15.4 GB RAM, integrated GPU), the real journey (data/prd_v7_audit/brow001_hybrid_journey_2026-09-28.json): one objective, web_fetch on the native-API path and browser_open, browser_inspect and browser_close on the DOM path, all SUCCEEDED and the run COMPLETED in 27 s; both paths read the page. The browser cost the machine 184 MB in its own processes and 140 MB of RAM at peak over the start. The live test that runs it passes (marked live: it needs the network and Chromium)."),
        "next_action": (
            "EXTEND: feed recorded task outcomes into rank() and let the planner pick a step's path with it; "
            "OWNER: install the browser extra in the product venv so its browser tools work."),
    }),
    # --- MCP-001: the MCP firewall, 2026-09-28
    ("MCP-001", {
        "status": PARTIAL,
        "current_implementation": (
            "friday/mcp_firewall.py pins an MCP server FRIDAY does not run itself the first time it is seen: "
            "the handshake server name, a binding (endpoint or executable hash, authenticated relationship, "
            "permissions, credential scope, risk class, workspace, sensitive capabilities, server metadata) and "
            "a hash of every tool's schema, description and authority, each description scanned with the skill "
            "linter's unsafe-instruction patterns first - a description that tries to instruct the model is "
            "refused before anything is pinned. The protocol is a policy (allowed eras and versions, the "
            "registered preference, the last version and era observed), never identity: a compatible new SDK "
            "or negotiated version is recorded and the tools are scanned again; a version outside the policy "
            "or an unapproved legacy/modern change suspends. A changed tool schema or description, an added or "
            "removed tool, a tool gaining authority, or a changed identity, endpoint, executable, credential "
            "scope or capability moves the server ACTIVE to SUSPENDED and withholds all its tools; it stays "
            "suspended even if it changes back, until the owner revalidates it (python -m friday.mcp_firewall "
            "revalidate URL). A server configured outside FRIDAY walks EXTERNAL_DISCOVERED, EXTERNAL_UNVERIFIED, "
            "VERIFIED_PENDING_APPROVAL, ACTIVE; only the owner, an admin policy or a lease approves or waives "
            "(a model's waiver is refused), and discovery reads Hermes' $HERMES_HOME/config.yaml mcp_servers "
            "(default and named profile) plus mcpServers JSON imports, from the CLI. The pins persist in "
            "data/mcp_pins.json. The voice agent's one MCP connection goes through it when MCP_URL is not "
            "loopback (the cloud-to-PC path), its tool output is typed untrusted, and a suspended server is "
            "reported as a safety stop, not an outage. Assumed: FRIDAY's own tool server on this machine is "
            "first-party code whose tools change with every update, so a loopback URL is not pinned; server "
            "metadata (the title and instructions a server hands the model) is authority-bearing, so a change "
            "suspends. Gaps: nothing in a live path calls inspect for a server Hermes or Claude Code workers "
            "configure, so the external lifecycle runs from the CLI and tests only, never yet against a live "
            "Hermes."),
        "files": ["friday/mcp_firewall.py:149,225,241,303,336,360,474,538,718,752,862,932", "agent_friday.py:739,1330"],
        "tests": ["tests/test_mcp_firewall.py::test_a_new_server_is_pinned_and_admitted",
                  "tests/test_mcp_firewall.py::test_an_unexpected_change_suspends_the_server",
                  "tests/test_mcp_firewall.py::test_a_description_that_instructs_the_model_is_refused_before_pinning",
                  "tests/test_mcp_firewall.py::test_revalidation_pins_what_the_server_now_offers",
                  "tests/test_mcp_firewall.py::test_fridays_own_server_on_this_machine_is_not_pinned",
                  "tests/test_mcp_firewall.py::test_the_voice_agent_withholds_a_suspended_servers_tools"],
        "evidence": (
            "2026-09-28: the tests failed first (no firewall; the agent loaded whatever the server listed). "
            "An unexpected schema change - and a rewritten description, an added and a removed tool - "
            "suspends the server and withholds its tools, and it stays suspended after changing back. Six "
            "mutations each turn a test red: changes ignored, the description scan skipped, a suspension not "
            "kept, every URL treated as first-party, revalidation activating without a fresh scan, and the "
            "agent loading tools despite a suspension."),
        "next_action": (
            "EXTEND: call Firewall.inspect from a live path for the MCP servers Hermes and Claude Code "
            "workers configure, and run the external lifecycle (discover, verify, approve, waive) against "
            "a live Hermes."),
    }),
    # --- SEC-003: FRIDAY's own state is not a file for its tools (V7-D56), 2026-09-29
    ("SEC-003", {
        "current_implementation": (
            "friday/self_upgrade.py:110-129 is_kernel_path() normalizes separators, case and dot-segments and "
            "matches KERNEL_PATHS; guard_kernel raises UpgradeRefused and journals refused_kernel, so a worker "
            "write to a kernel file is refused. The kernel's own state is now protected as well: from a source "
            "checkout the project directory is a file-tool root, and the file jail's denylist covered only "
            "secret-shaped files, so files_read, files_write and files_delete could reach the live store (which "
            "holds approval records), the audit trail, the consumed confirmation nonces (removing one would let "
            "a one-time confirmation be replayed), the MCP firewall pins and the logs (V7-D56). friday/fsjail.py "
            "now refuses any path inside the state root's data directory except FRIDAY's two work areas (its "
            "workspace and its artifacts), before containment and whatever roots are configured. The same "
            "change makes those work areas roots wherever the state root lives, which a packaged build needs "
            "(V7-D55)."),
        "files": ["friday/self_upgrade.py:84-129", "tests/test_self_upgrade.py:37-43",
                  "friday/fsjail.py:64,144,231,277", "tests/test_fsjail_state.py:22-42"],
        "tests": ["tests/test_self_upgrade.py::test_kernel_surface_is_refused",
                  "tests/test_fsjail_state.py::test_fridays_own_state_is_refused_even_inside_a_root",
                  "tests/test_fsjail_state.py::test_the_work_areas_inside_the_state_are_files_for_the_tools",
                  "tests/test_fsjail_state.py::test_the_work_areas_are_roots_wherever_the_state_lives"],
        "evidence": (
            "test_kernel_surface_is_refused: pytest.raises(su.UpgradeRefused) on guard_kernel(['friday/policy.py']) "
            "and on guard_kernel(['E:/x/friday/sensitive_domains.py']); assert 'refused_kernel' in the journal - "
            "an exact match for 'Worker write blocked.' Re-run 2026-09-25 on 030795f with a dirty tree: every "
            "cited test passed (data/prd_v7_audit/rerun.xml). "
            "2026-09-29 (V7-D56): the state tests failed first - each of the store, the audit trail, the nonce "
            "file, the MCP pins, the access log and a log file resolved inside a root. Three mutations each turn "
            "a test red: the own-state check removed from resolve(), the work areas no longer exempted, and the "
            "work areas no longer roots. The 18 test files that exercise the jail pass (275 passed, 2 skipped)."),
    }),
    # --- TEST-ISOLATION-001: a full suite run leaves the live state as it was, 2026-09-29
    ("TEST-ISOLATION-001", {
        "status": VERIFIED,
        "current_implementation": (
            "tests/conftest.py sets FRIDAY_HOME to a per-session temporary root before anything imports "
            "FRIDAY, so every path derived from the state root - the store, the audit log, the workspace, "
            "the nonce, cooldown and access files, the logs - is the session's, for the test process and "
            "every child that inherits the environment; the audit log follows ADA_AUDIT_DB as the store "
            "follows ADA_DB, so a test's child writes the test's audit log (V7-D52); opening a SQLite store "
            "under the live data directory is refused in the test process. scripts/baseline_suite.py "
            "compares the live data/ and logs/ before and after a run and fails the run - whatever the "
            "chunks did - when they changed and no live FRIDAY process was running. Residual: a few modules "
            "write repository-relative paths that ignore the state root (the deck queue, browser "
            "screenshots, the self-development journals), which the runner would catch but nothing "
            "prevents; a child started with a hand-built environment that leaves out FRIDAY_HOME would "
            "resolve the live root (the ones the suite starts today are third-party fabric children, not "
            "FRIDAY)."),
        "files": ["tests/conftest.py:39,61,71,239", "scripts/baseline_suite.py:82,127,214",
                  "friday/trust.py:264"],
        "tests": ["tests/test_test_isolation.py::test_the_session_runs_on_its_own_state_root",
                  "tests/test_test_isolation.py::test_a_process_a_test_starts_inherits_the_isolated_root",
                  "tests/test_test_isolation.py::test_opening_a_live_store_is_refused",
                  "tests/test_baseline_suite.py::test_the_run_names_every_live_file_it_changed",
                  "tests/test_baseline_suite.py::test_a_change_is_blamed_on_the_suite_only_when_friday_was_not_running",
                  "tests/test_baseline_suite.py::test_a_run_whose_chunks_pass_still_fails_when_live_state_changed",
                  "tests/test_trust.py::test_a_child_process_of_a_test_writes_the_tests_audit_log_not_the_live_one"],
        "evidence": (
            "2026-09-29: the child-process audit test failed first (V7-D52; in failing it wrote live audit "
            "row 486, which is kept and recorded with rows 480-485 as historical test contamination). The "
            "four-chunk suite over the follow-up tree with FRIDAY not running (data/baseline_v7_followup): "
            "all four chunks exit 0, 4,734 passed (1,279, 1,060, 1,371 and 1,024), none failed, and the "
            "runner's own check reads 'live_state unchanged (244 files under data/ and logs/)'. Four "
            "mutations each turn a test red: the live-store guard disabled, the verdict passing a changed "
            "state, the runner leaving the verdict out of its exit code, and the audit log ignoring "
            "ADA_AUDIT_DB. The FRIDAY_HOME line itself was not mutated: a run without it would point real "
            "tests at the live state, which is what this requirement forbids."),
        "next_action": (
            "EXTEND: move the repository-relative writers onto the state root, and give a child started "
            "with a hand-built environment the session's FRIDAY_HOME."),
    }),
    # --- Slice 7 (Amendment A4): pressure judged against the machine, 2026-09-30
    ("RES-003", {
        "status": VERIFIED,
        "current_implementation": (
            "friday/governor.py judges pressure against this machine, not fixed percentages. The primary "
            "classifier is the share of the machine's idle headroom in use - available RAM, the commit "
            "charge's room below its limit (Windows, GetPerformanceInfo) and CPU sustained over ten seconds - "
            "against a Baseline, the median of readings taken while FRIDAY holds no leased work: measured by a "
            "hardware qualification (RES-002) when one is stored for this machine, otherwise provisional and "
            "refined from the governor's own idle readings. RAM and commit read YELLOW from 40% of the idle "
            "headroom in use, ORANGE from 60% and RED from 80%; CPU from 60%, 80% and 95%. The trend: the "
            "least-squares slope of available RAM over the last minute; if it would reach the emergency floor "
            "within 60 s the level is ORANGE, within 300 s YELLOW. Static values are emergency guardrails only: "
            "RAM or sustained CPU at 95%, available RAM below max(0.5 GB, 3%), commit headroom below max(1 GB, "
            "3%), the disk at 1 or 5 GB. Hysteresis (Tracker): a worse level is entered at once; a better one "
            "only after the reading, judged with the share lines 5 points lower, has held for 30 s, and then to "
            "the worst level seen while waiting. GREEN, YELLOW, ORANGE and RED are NORMAL, ELEVATED, HIGH and "
            "CRITICAL, so the admission policy is unchanged. Residual: paging activity is not measured "
            "(psutil's page counters read 0 on Windows) and thermal state is not read; a provisional baseline "
            "is not kept across restarts, and the product does not yet qualify the machine on its first run "
            "(RES-004)."),
        "files": ["friday/governor.py:84,182,261,328,343,416,679", "scripts/stress_low_ram.py",
                  "data/prd_v7_audit/res003_stress_2026-09-30.json"],
        "tests": ["tests/test_governor.py::test_a_machine_idling_at_76_percent_reads_green",
                  "tests/test_governor.py::test_losing_headroom_fast_reads_pressured_before_any_fixed_line",
                  "tests/test_governor.py::test_a_machine_at_68_percent_losing_memory_fast_is_pressured",
                  "tests/test_governor.py::test_a_reading_wavering_at_a_boundary_does_not_flap",
                  "tests/test_governor.py::test_a_better_reading_must_hold_before_the_level_falls",
                  "tests/test_governor.py::test_the_level_falls_to_the_worst_reading_of_the_wait_and_needs_a_margin",
                  "tests/test_governor.py::test_a_cpu_spike_is_not_pressure_but_a_sustained_load_is",
                  "tests/test_governor.py::test_a_cpu_reading_right_after_another_is_not_a_new_measurement",
                  "tests/test_governor.py::test_fixed_percentages_are_emergency_guardrails_only",
                  "tests/test_governor.py::test_the_commit_charge_is_judged_against_its_own_idle_headroom",
                  "tests/test_governor.py::test_without_a_qualified_baseline_the_governor_profiles_the_machine_itself",
                  "tests/test_governor.py::test_real_memory_pressure_bounds_new_work"],
        "evidence": (
            "2026-09-30: the old classifier read REFERENCE-A at rest (76%) as ELEVATED ('RAM 76% used'), and the "
            "new tests failed against it. Real run on REFERENCE-A - one governor on the real sampler, the "
            "baseline measured at rest, no threshold moved (data/prd_v7_audit/res003_stress_2026-09-30.json): at "
            "rest the machine read GREEN at 89-90% RAM; memory allocated in 20 MB steps read YELLOW at 160 MB "
            "('available RAM falling 0.48 GB/min: the floor in 118 s', optional work shed) and ORANGE at 200 MB "
            "(the second worker queued) at 90.9% RAM, below every guardrail; after release ORANGE held 30.1 s and "
            "then fell; every admission matched the policy. The slow test that runs it passed on its own. The "
            "first real run was void and is not counted: a CPU reading 20 ms after the last said 100% and drove "
            "the peak (V7-D57, fixed); the harness now counts only memory-driven pressure. Eleven mutations each "
            "turn a test red: the baseline ignored, the trend removed, the hysteresis removed, the release margin "
            "removed, the worst-of-the-wait rule removed, a busy reading refining the baseline, a measured "
            "baseline refined, the commit guardrail removed, a static 75% line put back, CPU judged on one "
            "reading, and the CPU window removed. "
            "The four-chunk suite over the Slice 7 tree (data/baseline_v7_slice7a), FRIDAY not running: all four "
            "chunks exit 0, 4,753 passed (1,280, 1,073, 1,338 and 1,062), none failed, live state unchanged."),
        "next_action": (
            "EXTEND: measure paging activity (performance counters) and thermal state where the platform "
            "reports them; keep the refined baseline across restarts; run the stress on REFERENCE-B."),
    }),
    ("RES-002", {
        "status": PARTIAL,
        "current_implementation": (
            "friday/hardware_profile.py measures the machine: RAM, the idle baseline (the median of ten "
            "readings at rest), logical and physical CPUs, GPUs with their dedicated and shared memory through "
            "DXGI (the software renderer left out; None off Windows, never a guess), the state root's disk, the "
            "battery, and - with --browser - one headless Chromium's memory from BROW-004's readiness probe. The "
            "policy is derived from the measurements, not tiers: the room FRIDAY's work may take is RES-003's "
            "YELLOW share of the idle headroom; browsers are as many as fit at the measured cost within the "
            "governor's cap and never fewer than one; a local model's RAM budget is the room within A3's 2 GB per "
            "artifact, and GPU offload goes up to the largest GPU's dedicated memory. The profile is stored in "
            "the state root with a fingerprint of RAM, CPUs and GPUs; the process governor starts from its "
            "baseline and caps when it describes this machine, and otherwise profiles the machine from its own "
            "readings. Nothing is keyed to a hostname. Gaps: REFERENCE-B is not qualified; the product does not "
            "qualify the machine on its first run or after a change (RES-004); a worker's memory is not "
            "measured, so the worker cap stays FR-013's two; nothing benchmarks what models the machine can run "
            "(MODEL-009)."),
        "files": ["friday/hardware_profile.py:52,131,194,231,253,292,307,326", "friday/governor.py:679",
                  "data/prd_v7_audit/res002_profile_REFERENCE-A_2026-09-30.json"],
        "tests": ["tests/test_hardware_profile.py::test_this_machine_is_measured_and_nothing_is_guessed",
                  "tests/test_hardware_profile.py::test_two_reference_machines_get_different_profiles_and_policies_from_the_same_code",
                  "tests/test_hardware_profile.py::test_browsers_are_as_many_as_the_room_holds_at_the_measured_cost",
                  "tests/test_hardware_profile.py::test_the_governor_starts_from_the_stored_profile_of_this_machine",
                  "tests/test_hardware_profile.py::test_a_profile_of_another_machine_is_not_used",
                  "tests/test_hardware_profile.py::test_the_command_line_qualifies_and_shows"],
        "evidence": (
            "2026-09-30: the tests failed first (no module). REFERENCE-A qualified from FRIDAY's own environment "
            "(data/prd_v7_audit/res002_profile_REFERENCE-A_2026-09-30.json): 15.35 GB RAM with 2.11 GB available "
            "at rest (the owner's programs open), 16 logical and 8 physical CPUs, an AMD Radeon integrated GPU "
            "with 0.48 GB dedicated and 7.68 GB shared, on battery at 68%, one headless Chromium 265 MB; policy: "
            "0.84 GB of room, 2 browsers, a model RAM budget of 0.84 GB, GPU offload up to 0.48 GB. The same code "
            "on REFERENCE-B's hardware as Amendment A4 describes it derives a 2 GB model budget and 2 GB of "
            "offload (test). Seven mutations each turn a test red: a RAM change not detected, a profile of "
            "another machine used, the governor ignoring the stored profile, the software renderer counted as a "
            "GPU, offload not bounded by the GPU's memory, zero browsers on a small machine, and the room taken "
            "from total RAM instead of the idle headroom. "
            "The four-chunk suite over the Slice 7 tree (data/baseline_v7_slice7a), FRIDAY not running: all four "
            "chunks exit 0, 4,753 passed (1,280, 1,073, 1,338 and 1,062), none failed, live state unchanged."),
        "next_action": (
            "OWNER: on REFERENCE-B run `python -m friday.hardware_profile qualify --browser --no-save --out "
            "<file>` from FRIDAY's .venv. EXTEND: qualify on first run and on a material change (RES-004), "
            "measure a worker's memory, and benchmark models (MODEL-009)."),
    }),
    ("RES-001", {
        "status": PARTIAL,
        "current_implementation": (
            "The resource governor (friday/governor.py) samples RAM, the commit charge, CPU, disk and browser "
            "processes and admits, queues or sheds new work by pressure level: ELEVATED sheds optional work, "
            "HIGH holds concurrency to one worker, CRITICAL starts nothing new; the level is judged against the "
            "machine's own baseline (RES-003). scripts/stress_low_ram.py drives real memory pressure against one "
            "governor judging against the baseline measured at rest, with no threshold moved: a child process "
            "allocates and touches memory in steps sized to the machine, bounded by a cap and a 1 GB safety "
            "floor, stopping once memory has held concurrency to one worker, and every decision is checked "
            "against the policy before and after the memory is released. Gaps: GPU, network and local-model "
            "residency (MODEL-005) are not sampled or governed; work already running is not reduced under "
            "pressure, only new work refused or queued; section 55's owned-process record (owner, objective, "
            "lease, heartbeat, deadline, cleanup) is not checked here."),
        "files": ["friday/governor.py:84,343,416,581", "scripts/stress_low_ram.py",
                  "data/prd_v7_audit/res001_low_ram_2026-09-27.json",
                  "data/prd_v7_audit/res003_stress_2026-09-30.json"],
        "tests": ["tests/test_governor.py::test_real_memory_pressure_bounds_new_work",
                  "tests/test_governor.py::test_high_pressure_reduces_concurrency_to_one",
                  "tests/test_governor.py::test_critical_pressure_sheds_all_new_work"],
        "evidence": (
            "2026-09-27, real memory on this 15.4 GB host (data/prd_v7_audit/res001_low_ram_2026-09-27.json), "
            "with the thresholds then set a few points above the load at the start: 300 MB moved the governor to "
            "ELEVATED, 600 MB to HIGH, 750 MB to CRITICAL; once released it admitted all three again; every "
            "decision matched the policy. 2026-09-30, rerun against the machine's own baseline with nothing "
            "moved (data/prd_v7_audit/res003_stress_2026-09-30.json): YELLOW then ORANGE on real memory before "
            "any fixed line, every decision as the policy says; the slow test passed on its own. "
            "The four-chunk suite over the Slice 7 tree (data/baseline_v7_slice7a), FRIDAY not running: all four "
            "chunks exit 0, 4,753 passed (1,280, 1,073, 1,338 and 1,062), none failed, live state unchanged."),
        "next_action": (
            "EXTEND: sample GPU and network, govern local-model residency with MODEL-005, reduce running "
            "optional work under pressure, and record section 55's owned-process fields for every spawned "
            "process."),
    }),
    # --- RES-004: the profile kept current, 2026-09-30
    ("RES-004", {
        "status": PARTIAL,
        "current_implementation": (
            "friday/hardware_profile.py refresh() brings the stored profile up to date with this machine and "
            "measures only what changed: with no profile, or a RAM total, CPU count or GPU set that differs, it "
            "measures the machine again - its facts, the idle baseline and the policy; with a changed power "
            "source (mains or battery) it updates the power state and derives the policy again, leaving the "
            "baseline. Each pass is recorded in the profile's history with its cause and what was measured "
            "again (the last 20 kept), and the process governor adopts the new baseline and caps. A pass is "
            "postponed while FRIDAY holds leased work, so the baseline is never taken under FRIDAY's own load. "
            "server.main starts watch(), which runs the pass 30 s after start and every five minutes; a failed "
            "pass is logged and retried, never stopping the server. Gaps: no real hardware change or power flip "
            "has been exercised on a machine yet (the tests stand the machine in); the power state does not "
            "yet change what background work may run (BG-001); the voice agent's process keeps its own "
            "governor and does not run the watch."),
        "files": ["friday/hardware_profile.py:340,349,387,392,405", "server.py:32"],
        "tests": ["tests/test_hardware_profile.py::test_the_first_run_qualifies_the_machine_and_records_why",
                  "tests/test_hardware_profile.py::test_a_hardware_change_is_measured_again_with_its_cause",
                  "tests/test_hardware_profile.py::test_a_power_change_rederives_without_measuring_again",
                  "tests/test_hardware_profile.py::test_the_running_governor_adopts_a_new_profile",
                  "tests/test_hardware_profile.py::test_a_busy_governor_postpones_the_measurement",
                  "tests/test_entry_points.py::test_the_server_honours_an_emergency_stop"],
        "evidence": (
            "2026-09-30: the tests failed first (no refresh). Three mutations each turn a test red: a power "
            "change ignored, a busy governor not postponing the measurement, and the server not starting the "
            "watch. The affected test files pass (entry points, hardware profile, governor, reachability: 67). "
            "Not yet in a four-chunk run; the next checkpoint's suite covers it."),
        "next_action": (
            "EXTEND: exercise a real power flip on REFERENCE-A (unplug while the server runs) and record the "
            "history entry; let the power state gate background work (BG-001)."),
    }),
    # --- BROW-001: recorded routing outcomes in the path order, 2026-09-30 (first build-kernel worker)
    ("BROW-001", {
        "status": PARTIAL,
        "current_implementation": (
            "friday/action_path.py is section 48's router: each capability acts through a path - native API, "
            "official scripting, a structured tool or MCP, the DOM, semantic vision or screen coordinates - and "
            "rank() puts a step's candidates most direct path first, moving a path that has kept failing (five "
            "tries or more, under half succeeding) behind the others. Capability search offers tied matches in "
            "that order without changing what is offered, and a tie now also moves last a capability that has "
            "kept being the wrong prediction for this request shape: routing_memory.reliability() tallies the "
            "settled shadow predictions per capability (right or wrong for the request). "
            "scripts/journey_hybrid_browser.py runs one objective that reads a page over HTTP and then opens, "
            "reads and closes it in a headless Chromium through the real executor; the browser extra is "
            "installed in the product's own .venv and reads BROWSER_READY (BROW-004). Gaps: the outcome that "
            "feeds rank() is routing correctness, not whether running a path succeeded - execution outcomes "
            "do not feed it yet; the planner does not choose between alternative paths for a step."),
        "files": ["friday/action_path.py:25,45,58,66", "friday/capability_router.py:545",
                  "friday/routing_memory.py:133", "scripts/journey_hybrid_browser.py", "pyproject.toml:57"],
        "tests": ["tests/test_action_path.py::test_one_objective_runs_a_structured_step_and_a_real_browser_step",
                  "tests/test_action_path.py::test_the_order_is_section_48s",
                  "tests/test_action_path.py::test_a_step_takes_the_most_direct_path_first",
                  "tests/test_action_path.py::test_a_path_that_keeps_failing_moves_behind_the_others",
                  "tests/test_capability_router.py::test_a_tie_in_search_goes_to_the_more_direct_path",
                  "tests/test_capability_router.py::test_the_path_order_never_changes_what_is_offered",
                  "tests/test_capability_router.py::test_a_tie_moves_a_path_that_keeps_failing_for_this_request_behind"],
        "evidence": (
            "2026-09-27/28: the action-path tests failed first; the real hybrid journey passed on REFERENCE-A "
            "(data/prd_v7_audit/brow001_hybrid_journey_2026-09-28.json). 2026-09-30: the routing-outcome change "
            "was made by the first build-kernel worker (headless Sonnet in its own worktree: 23 turns, 150 s, "
            "$0.36) and verified by the controller: disabling the reliability input turns the new test red; the "
            "13 directly affected test files and the reachability test pass in the main checkout (287)."),
        "next_action": (
            "EXTEND: feed execution outcomes (a path's recorded successes and failures when run) into rank(), "
            "and let the planner choose a step's path with them."),
    }),
    ('BROW-001', {
        "status": PARTIAL,
        "current_implementation": "Execution outcomes (SUCCEEDED/FAILED tasks per capability) feed action_path.rank via routing_memory.execution_reliability; router search and the planner's tie-break use them. Live golden journey (web_fetch + headless Chromium) ran and passed; duplicate test name fixed. (build run, commit 3a27257f8)",
        "next_action": 'GAPS: execution_reliability is a per-capability tally over all runs, not scoped to a request shape or recency window; chosen because execution success is mostly a property of the capability, and rank() only reorders ties, never changes what is offered. Scoping needs an owner decision on a run-to-shape key.',
    }),
    ('BROW-001', {
        "status": VERIFIED,
        "current_implementation": 'Recorded execution outcomes (SUCCEEDED/FAILED objective tasks per capability) feed action_path.rank via routing_memory.execution_reliability; router search merges them with routing outcomes and the planner breaks fit ties by path order and execution reliability (3a27257).',
        "next_action": 'Refinement outside the acceptance (ruling 2026-10-01): scope execution reliability per request shape once a run-to-shape key exists; rank() only reorders ties today.',
        "evidence": 'Golden journey: tests/test_action_path.py::test_one_objective_runs_a_structured_step_and_a_real_browser_step (live: a structured web_fetch step and a headless-Chromium step in one objective) passed on the integrated tree 469cd13, re-run by the build controller with an isolated state root (1 passed, 11.4 s). Build run commit 3a27257: RED (assertion) at a5a459f and GREEN at the commit for tests/test_action_path.py, tests/test_capability_router.py; affected tests passed; fresh verifier PASS (sonnet, $0.13).',
    }),
    ('CORE-001', {
        "status": PARTIAL,
        "current_implementation": "First build attempt (branch attic/core-001-v1, commit 0cb6741): a Hermes COMPLETE meets FRIDAY's verifier when a verify command is supplied (verify_command or FRIDAY_DELEGATION_VERIFY_COMMAND); otherwise it is relayed COMPLETE with verification NOT_VERIFIED. Rejected twice by the fresh verifier; not integrated.",
        "next_action": "EXTEND per OWNER RULING 2026-10-02: when a delegation carries no verify command, a model derives a check from the objective's free-text acceptance. Guardrails: the derived check is a command from an allowlist of test runners only (e.g. pytest, npm test, cargo test), executed through FRIDAY's execution policy inside the delegation workspace jail (no network), recorded with provenance 'derived' in the verdict; if no check can be derived or it fails, the result is NOT_VERIFIED and the objective is not done. Also fix what the verifier found in attempt 1: the verdict must be written to the objective store (not only the hermes run record); the verifier must not run on the gateway reader thread; find why an audit.sqlite3 appears in the delegation workspace (audit isolation). Reuse attempt 1's code from attic/core-001-v1 where it fits.",
    }),
    ('DOMAIN-001', {
        "status": PARTIAL,
        "current_implementation": 'Added friday/domain_gate.py: deterministic restricted-domain classifier, persisted safeguard-event ledger, bounded provider-refusal count then switch_route. Wired into HermesSupervisor.delegate and ClaudeCodeExecutor.execute before any lease/session/launch. (build run, commit 0e92e8ce0)',
        "next_action": 'GAPS: Restricted-domain phrase list (cbrn, offensive_cyber) is an ASSUMPTION pending owner ruling; one tuple per domain in domain_gate.RESTRICTED_DOMAINS.; record_provider_refusal is not yet called from a live provider-failure path; only the gate side (refuse after 2) is wired.; provider_health.py and store.py untouched; ledger is its own sqlite file (FRIDAY_SAFEGUARD_DB or data/safeguard_events.sqlite3).',
    }),
    ('DOMAIN-001', {
        "status": PARTIAL,
        "current_implementation": 'Added friday/domain_gate.py: deterministic restricted-domain classifier, persisted safeguard-event ledger, bounded provider-refusal count then switch_route. Wired into HermesSupervisor.delegate and ClaudeCodeExecutor.execute before any lease/session/launch. (build run, commit 0e92e8ce0)',
        "next_action": "OWNER RULING 2026-10-02 (docs/plans/build-harness/00-owner-directive-2026-10-02f.md, message 2): replace the worker's assumed labels. Top-level domains are 'cbrne' (keep 'cbrn' as an alias) and 'cyber_sensitive' - never 'offensive_cyber'. Cyber gets a second, separate classification of intent and authority: defensive (normal secure route), authorized_testing (Cyber Lab + authorization contract), malicious_or_abusive (deny/restrict), unknown (bounded; ask for scope when needed). Phrases ('kali', 'nmap', 'offensive security') are signals only, combined with task intent, target ownership/authorization, requested action and execution environment; the final routing decision is deterministic policy, not model prose. Tests, positive and negative controls: (1) a SQL-injection code review is defensive, not malicious; (2) a pentest of the person's own staging server with a stated scope is cyber_sensitive + authorized_testing -> Cyber Lab; (3) breaking into a third-party server is malicious_or_abusive -> restricted; (4) the history of CBRN arms-control treaties is informational, not weapon enablement, despite the keyword; (5) a CBRNE-sensitive request without the literal keyword is still classified by the semantic path - use an injectable classifier and a synthetic stand-in fixture, never real enablement content in the repository; (6) 'Offensive Security conference notes' is not malicious cyber. Still open from the build run: record_provider_refusal is not called from a live provider-failure path.",
    }),
    ('CORE-001', {
        "status": PARKED_BY_OWNER,
        "current_implementation": "First build attempt (branch attic/core-001-v1, commit 0cb6741): a Hermes COMPLETE meets FRIDAY's verifier when a verify command is supplied (verify_command or FRIDAY_DELEGATION_VERIFY_COMMAND); otherwise it is relayed COMPLETE with verification NOT_VERIFIED. Rejected twice by the fresh verifier; not integrated.",
        "next_action": "PARKED_BY_OWNER 2026-10-02 (docs/plans/build-harness/00-owner-directive-2026-10-02f.md and the product-first override): not cancelled, not verified. Resume only on the owner's word, or when other important product work is exhausted and memory admits it. Then: build_kernel.py escalate CORE-001 --model opus --packet docs/plans/build-harness/core-001-escalation-packet.md (fresh session, the four unresolved conditions only: jailed derived check, audit.sqlite3 root cause, no acceptance contract is never COMPLETE, verdict persisted only when correlated to the objective run). State kept: branch build/core-001 (e49549f, rebased on de18900), worktree .worktrees/core-001, three verifier receipts in data/build_runs/.",
    }),
    ('PKG-001', {
        "status": PARTIAL,
        "current_implementation": 'scripts/validate_exe_clean.py copies the bundle to a fresh profile on this host and passes version, paths, doctor, MCP plus control room, and stop (docs/build/FRIDAY_EXE_VALIDATION.md:25,81-87); state lives under LOCALAPPDATA (tests/test_state_root.py). Not demonstrated: a clean Windows VM - the validation doc lists a true fresh-OS run as not done (:143) - and bootstrap does not provision Hermes, a browser runtime or a coding CLI (CLEAN-03 reports them DEGRADED; v1 FR-ONB-001 is PARTIAL).',
        "next_action": "DEFECT found running FRIDAY.exe 344d62a (2026-10-02): `--doctor` reports PASS for credentials that are template placeholders (LIVEKIT_URL wss://your-project-..., LIVEKIT_API_KEY/SECRET, OPENAI_API_KEY) and for a GOOGLE_API_KEY Google rejects with 401 - it only checks that values are non-empty, so 'configured' was reported as 'usable'. The launcher then starts the LiveKit voice worker, which retries the placeholder URL forever with 401. Fix: the doctor classifies each credential as MISSING / PLACEHOLDER / REJECTED (a bounded live auth probe, value never printed or logged) / VALID and reports DEGRADED naming which; the launcher skips the voice worker with one clear line when the LiveKit credentials are not VALID (the control room keeps its own browser voice); onboarding (ONB-001) asks for the missing keys. Tests: a placeholder and a rejected key each turn the doctor DEGRADED (fails without the fix); the launcher does not start the agent on placeholder LiveKit credentials. Previous next action: Add a provisioning stage (Hermes, browser runtime, coding CLI) with PASS/DEGRADED/FAIL and a repair path per component, then run validate_exe_clean.py on a fresh Windows VM or Windows Sandbox and record the ready-state.",
    }),
    ('DOMAIN-001', {
        "status": PARTIAL,
        "current_implementation": 'Gate now uses owner labels cbrne (cbrn alias) and cyber_sensitive, with a deterministic cyber intent split (defensive/authorized_testing/malicious_or_abusive/unknown), injectable semantic classifier, and live provider-refusal counting in the CLI executor. (build run, commit f43a83cea)',
        "next_action": 'GAPS: Cyber Lab route is a label only: no Cyber Lab executor or authorization-contract module exists yet; record_provider_refusal is wired for the CLI executor only; Hermes sessions have no refusal hook; test_claude_code_executor not run',
    }),
    ('ACT-001', {
        "status": PARTIAL,
        "current_implementation": 'contracts.ActionResult refuses status succeeded without a verification at construction; honesty.py matches claims to evidence. Gap: the per-type proof set (file hash/readback, code diff and tests, web post-state, email message id, simulation artifact) and the full correlation key are not demonstrated as a whole.',
        "next_action": "PARKED after five build attempts (2026-10-02); branch build/act-001 (9739a8d) holds the library work and the live wiring, not integrated. The verifier accepts the library (typed proof shapes keyed to the real ids executor.claude_code*, executor.hermes, cad.generate; correlation enforced; failure claims need a recorded attempt). Open: (1) BUG on the wired voice path - a capability that raises records its FAILED attempt but is never added to _acted_this_turn (agent_friday.py:1105-1119), so an honest failure report is refused; add the browser path's test (a failure claim matching a recorded failure passes) for voice; (2) live audits run strict_proof=False because the live paths only hold tool text - per-type proof needs tools and executors to emit structured proof tokens (sha256=, diff_files=, tests_passed=, post_state=, message_id=); (3) executors do not record test results, so a real executor.claude_code success cannot satisfy the code proof shape. (2)+(3) are a cross-cutting design change: resume ACT-001 with an advisor (strong model) design packet for structured tool proof, then a fresh implementer on top of the branch.",
    }),
    ('DOMAIN-001', {
        "status": PARTIAL,
        "current_implementation": 'Hermes sessions now count provider safety refusals against the admitted route (same ledger/key the gate reads), closing the Hermes refusal-hook gap. CLI executor and neighbouring executor suites run green. (build run, commit 520383799)',
        "next_action": 'GAPS: Cyber Lab route is still a label only: no Cyber Lab executor or authorization-contract module exists; its contents need an owner decision',
    }),
    ('MCP-001', {
        "status": PARTIAL,
        "current_implementation": "Firewall now pins the handshake server name/version (change suspends), types a non-loopback server's tool output as untrusted via a result resolver, and builds the MCP client through mcp_firewall.server(). (build run, commit 15aff6531)",
        "next_action": 'GAPS: 3 failures in tests/test_voice_path.py are an invalid OpenAI API key (401) in this environment, unrelated; TLS identity is still not pinned; MCP servers used by Hermes/Claude Code workers are configured outside FRIDAY and not seen; wrapped output is a JSON envelope the model is told to treat as data; the runtime does not enforce it beyond that',
    }),
    ('DOMAIN-001', {
        "status": VERIFIED,
        "current_implementation": 'Hermes sessions now count provider safety refusals against the admitted route (same ledger/key the gate reads), closing the Hermes refusal-hook gap. CLI executor and neighbouring executor suites run green. (build run, commit 520383799)',
        "next_action": "Ruling 2026-10-02: the Cyber Lab executor and authorization-contract module are CYBER-002's scope; DOMAIN-001 routes restricted packets to their own program and never retries a refused route.",
        "evidence": "Owner ruling 2026-10-02 implemented (cbrne with cbrn alias, cyber_sensitive with a separate intent/authority classification; phrases are signals, deterministic policy decides). The owner's six positive and negative controls plus a no-scope case pass on the integrated tree (17 passed, re-run by the controller with an isolated state root). Build runs f43a83c and 5203837: RED/GREEN at base, affected tests green, fresh verifier PASS both times.",
        "tests": ['tests/test_domain_gate.py::test_sql_injection_code_review_is_defensive_not_malicious', 'tests/test_domain_gate.py::test_pentest_of_own_staging_with_scope_routes_to_cyber_lab', 'tests/test_domain_gate.py::test_breaking_into_a_third_party_server_is_restricted', 'tests/test_domain_gate.py::test_arms_control_treaty_history_is_informational', 'tests/test_domain_gate.py::test_semantic_classifier_catches_a_packet_without_the_keyword', 'tests/test_domain_gate.py::test_offensive_security_conference_notes_are_not_malicious'],
        "files": ['friday/domain_gate.py'],
    }),
    ('CYBER-002', {
        "status": PARTIAL,
        "current_implementation": 'trust.SecurityAuthorization requires owner, scope, expiry and approval id and target_guard refuses out-of-scope hosts; contracts.NET_SECURITY_TARGETS represents the network scope; the fabric registers restricted providers with required permissions. Missing: a provider interface, a sandbox tier abstraction, a mock provider and an evidence contract for the lab.',
        "next_action": "Add the provider interface, tier abstraction and a mock provider behind the existing authorization contract, with deterministic policy tests only (domain partition, section 95.2). | Carried from DOMAIN-001 (2026-10-02): domain_gate routes cyber_sensitive + authorized_testing packets to a 'cyber_lab' label, but no Cyber Lab executor or authorization-contract module exists yet - build them here.",
    }),
    ('APPROVAL-001', {
        "status": PARTIAL,
        "current_implementation": "A person's yes to a pending objective action is a lease on the plan it was given for: resume_after_approval records it with the objective's plan hash (each task's capability, arguments and dependencies, without the approval marker; objectives.plan_hash), its graph version (modification.graph_version) and an expiry (objectives.APPROVAL_TTL, 24 hours). The runtime honours it (capability_runtime._approval_on_record) only for the exact capability and arguments, while unexpired, and while the plan hash is unchanged: a task added or an argument amended afterwards, an expired yes, or a record from before leases (no plan hash) no longer authorises, and the run asks again. A standing permission stays a policy rule, not an approval. Gap: blast radius is not in the binding, because SIDE-002's blast-radius estimate does not exist yet.",
        "next_action": "RULING 2026-10-02 (controller, reversible): per-action blast radius derives from the capability's declared effect profile (CAP-004: read_only, consequential, reversible, idempotent, external_side_effect) combined with the action's target scope (how many targets, whose data); execution_economics.py classifies tasks for cost, not authority, and is not the source. So this no longer waits on SIDE-002: add blast_radius to the lease binding computed that way, make a change of blast radius a material change (the approval goes STALE), and test both. The lease binding (objective, version, plan hash, expiry) already holds - its 7 tests pass (re-run 2026-10-02).",
    }),
    ('CTX-001', {
        "status": PARTIAL,
        "current_implementation": "The working set's context telemetry is now stored with the gateway's call row (context_sections column, migrated onto old ledgers), carried on the request from model.infer. (build run, commit 32225635e)",
        "next_action": 'GAPS: no model route context profile sets a budget yet; Hermes task bundle and voice turn still assemble context outside the compiler; test_live_hermes_gateway_answers_without_an_agent_loop (live-marked) fails on TEST-ISOLATION-001 opening data/gateway_calls_live_test.sqlite3; not touched by this change',
    }),
    ('BCON-001', {
        "status": PARTIAL,
        "current_implementation": "Contract governance holds a run to data invariants (checked on arguments before and results after each step), to state preconditions (governance 'requires', gating side-effecting actions only, or those named by an optional 'before'), and to declared recovery (resume_failed_items_only retries only the failed items, reading per-item outcomes from the result's output envelope and keeping merged outcomes on the task). Invariants and recovery are parsed from the request text, ignoring prose like 'the email must be short'. (build run, commit 92630f1bc)",
        "next_action": "GAPS: a state precondition said in words ('once the invoice is approved') is not parsed from text; it is governance-only; max_external_writes from the acceptance criteria is enforced under the key max_side_effects; of the ~110 files test_impact.py names, ~25 were run (contract, objective, planner, continuity, trajectory, executor suites); the rest were not run",
    }),
    ('CTX-001', {
        "status": PARTIAL,
        "current_implementation": "model.infer now compiles its working set within the task class's max_input_tokens, so over-budget context sheds low-priority sections and an unfittable P0 fails before the gateway. (build run, commit ec43bf3cc)",
        "next_action": 'GAPS: Hermes task bundle and voice turn still assemble context outside the compiler; live-marked test_live_hermes_gateway_answers_without_an_agent_loop fails on TEST-ISOLATION-001 (untouched)',
    }),
    ('CTX-003', {
        "status": PARTIAL,
        "current_implementation": "The Claude Code development packet carries and checks the objective's critical facts. Plan amendments are now recorded as decisions (read from the graph_edited events into CriticalFactSet.decisions, so they are binding and checked). A DevelopmentRun built with store and objective_id reads the facts itself in execute, so no caller has to remember to pass facts=. The checkpoint summary is stored whole instead of cut at 2,000 chars. (build run, commit 3042f8685)",
        "next_action": 'GAPS: No production code calls development.for_goal or DevelopmentRun.execute at all today (only the reachability entry-point list names them), so the wiring is proven by test, not by a live run; whoever adds the caller must pass store= and objective_id= to for_goal. Needs an owner decision on where the objective engine hands work to the development path.; Checkpoint summary is stored uncapped; if a size bound is wanted it is an owner decision, since a cut summary can lose facts silently.',
    }),
    ('CYBER-002', {
        "status": PARTIAL,
        "current_implementation": 'Added friday/cyber_lab.py: LabProvider protocol, sandbox tiers capping intensity, registry, MockProvider, hash-bound evidence; all calls gated by trust.target_guard. (build run, commit 918b4af14)',
        "next_action": 'GAPS: Not wired into fabric descriptors or a live executor; cyber_lab domain_gate label has no caller yet',
    }),
    ('BCON-001', {
        "status": PARTIAL,
        "current_implementation": "A state precondition said in words ('once the invoice is approved') is now parsed from the request into governance 'requires' as invoice_status=approved; prose like 'when it is ready' is ignored. (build run, commit c11511832)",
        "next_action": "GAPS: Assumed the field for a spoken precondition is '<noun>_status' with a fixed list of state words; revisit if results use another field name; max_external_writes is still enforced under the key max_side_effects (same limit, different name); Only the 8 test files test_impact names for planner.py were run, not the ~110 from the earlier wave",
    }),
    ('CYBER-002', {
        "status": PARTIAL,
        "current_implementation": 'Added cyber_lab.run_routed: a domain_gate cyber_lab decision is the only route that reaches a lab provider; other routes or unknown providers yield hash-bound refusal evidence. Contract and tier gating unchanged. (build run, commit f9ca71f8c)',
        "next_action": 'GAPS: Not wired into fabric descriptors or a live executor; nothing outside tests calls run_routed yet',
    }),
    ('DUR-001', {
        "status": PARTIAL,
        "current_implementation": 'Side-effecting calls get a task-stable idempotency_key (to ports that accept it), and O.replay_task_statuses rebuilds task statuses from events alone, treating a commit as SUCCEEDED. (build run, commit ea4da455b)',
        "next_action": 'GAPS: No production capability yet honours idempotency_key, so the call-returned-but-uncommitted instant still re-runs; Replay covers task statuses only and is not used to restore state; task rows remain the state',
    }),
    ('INTENT-001', {
        "status": PARTIAL,
        "current_implementation": 'Contract now records assumptions (understand) and open questions (still_blocking) at objective_start; admit_objective holds a request while a blocking question is unanswered (V7-D38) and the live loop asks it. (build run, commit 5168d9e99)',
        "next_action": 'GAPS: 3 failures in tests/test_voice_path.py (live TTS/STT, LiveKit placeholders in .env) are environmental, not from this change; understand() is only called for idea-shaped text and needs a model provider; with none, assumptions stay empty',
    }),
    ('JIT-001', {
        "status": PARTIAL,
        "current_implementation": "Added an authority-probe seam to CapabilityRuntime: a capability's owner registers probes for authentication/target state; execute re-asks them at the moment of action and refuses 'JIT: ...' (a probe that raises also refuses). (build run, commit 14428552a)",
        "next_action": 'GAPS: No probe is registered for any real capability yet; requires_auth capabilities still need their owners to register one; Envelopes from before fingerprints carry no hashes',
    }),
    ('DUR-001', {
        "status": PARTIAL,
        "current_implementation": "The production objective port now honours idempotency_key: it records a successful call's result as a call.returned event under the key and replays it on re-run, so a death between call return and the executor's commit no longer repeats the side effect. (build run, commit 0c0f39049)",
        "next_action": "GAPS: A crash between the external effect and the port's call.returned write still re-runs the call; only a capability that dedupes by key itself closes that; Replay covers task statuses only and is not used to restore state; task rows remain the state",
    }),
    ('JIT-001', {
        "status": PARTIAL,
        "current_implementation": "Registered real JIT target-state probes for files_edit/copy/move/recycle/delete: a file gone or outside the jail at execution is refused 'JIT: ...'. Hash-change acceptance already covered by existing tests. (build run, commit e691d993f)",
        "next_action": 'GAPS: requires_auth capabilities other than files_* still have no probe; there is no live-session signal to probe for them; Envelopes from before fingerprints carry no hashes',
    }),
    ('MEM-002', {
        "status": PARTIAL,
        "current_implementation": 'The voice agent now tracks whether a turn consumed a tool result or page and sets from_read_material on memory_remember/memory_record_decision/brain_remember; such writes are READ_MATERIAL, refused as preference/decision/overrule, and the ledger refuses them. (build run, commit e7eb76773)',
        "next_action": "GAPS: Only the agent's use_capability path sets the flag; a client calling the MCP memory tools directly can still omit it (fail-open). Taint is per turn and conservative: any capability result marks the turn read.; Agent-level wiring is covered by a constant check, not an end-to-end use_capability test.",
    }),
    ('MEM-002', {
        "status": PARTIAL,
        "current_implementation": "memory_remember, memory_record_decision and brain_remember now fail closed: a direct MCP client that omits from_read_material is treated as read material; only an explicit False (set by the voice agent) is the person's voice. (build run, commit 4b705d3a6)",
        "next_action": "GAPS: Taint is per turn and conservative: any capability result marks the turn read.; Agent-level wiring is covered by a constant check, not an end-to-end use_capability test.; A direct client now cannot write the person's preferences or decisions via these tools unless it passes from_read_material=false explicitly.",
    }),
    ('RES-001', {
        "status": PARTIAL,
        "current_implementation": 'Governor samples GPU/network, governs model residency, stops running optional/surplus work, and records section 55 fields; Hermes/Claude send heartbeats, stop is non-blocking, double release keeps the queue, stress test stops a real helper. (build run, commit 4e6096455)',
        "next_action": 'GAPS: GPU is read only through nvidia-smi.; No worker operation exists to unload a resident local model.; HIGH/CRITICAL reduction is tested only with synthetic samples.',
    }),
    ('TRAJ-001', {
        "status": PARTIAL,
        "current_implementation": 'Trajectory check now reads the objective contract: forbidden action classes started, side-effect count over max_side_effects, and side-effecting steps started while contract questions are open flag the run DRIFTING. (build run, commit 361b426b8)',
        "next_action": 'GAPS: user-amendment and worker-conflict signals (no amendment event exists yet); non-pause actions (fresh worker, stronger model, lower concurrency); executor still only pauses and asks; no executor-level test of a contract-flagged pause; only assess/for_run inputs covered by unit tests',
    }),
    # --- CTX-002: owner ruling on the learned retriever, 2026-10-02
    ('CTX-002', {
        "status": PARTIAL,
        "next_action": (
            "RULING 2026-10-02 (owner, docs/plans/build-harness/00-owner-directive-2026-10-02h.md): reach "
            "the target with small local models, not a hosted API and not by relabelling the dev split. "
            "START: `git checkout wip/ctx-002-retrievers -- friday/tool_retrieval.py "
            "tests/test_tool_retrieval.py scripts/bench_tool_disclosure.py` (the BLOCKED run's BM25, "
            "hashed-dense and hybrid retrievers and benchmark; none of these paths changed on main since). "
            "STATICALLY_CONFIRMED by the controller: onnxruntime, tokenizers, huggingface_hub and numpy are "
            "in .venv and .venv-build, so install nothing. Embedding BAAI/bge-small-en-v1.5 (MIT) revision "
            "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a, onnx/model.onnx sha256 "
            "828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35; reranker "
            "cross-encoder/ms-marco-MiniLM-L6-v2 (Apache-2.0) revision "
            "233902d25c440f23af6f7d6e94d2946bac0bee0a, onnx/model.onnx sha256 "
            "5d3e70fd0c9ff14b9b5169a51e957b7a9c74897afd0a35ce4bd318150c1d4d4a; both tokenizer.json sha256 "
            "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66. Both are already downloaded "
            "in huggingface_hub cache layout under F:/F.R.I.D.A.Y/cache/models: hf_hub_download(repo, file, "
            "revision=<pin>, cache_dir=<that dir>) returns them with no network. Both take input_ids, "
            "attention_mask and token_type_ids; the embedder outputs last_hidden_state (take the CLS row, "
            "L2-normalise; bge's query prefix is 'Represent this sentence for searching relevant passages: '), "
            "the reranker outputs logits [batch, 1] for (query, document) pairs. Together they load in 1.5 s "
            "and add 240 MB RSS; three rerank pairs take 22 ms. "
            "DESIGN: the models dir is FRIDAY_MODELS_DIR, else config.CACHE_DIR / 'models' (add cache/ to "
            ".gitignore). Register each model in the capability fabric as its own provider (upstream = the "
            "Hub repo id, commit = the revision pin, PERMISSIVE_IMPORT, ADAPTER) with a provision operation "
            "that downloads by pin and verifies the sha256, and a runtime load with local_files_only, lazily, "
            "once, on CPU: files absent or a hash mismatch -> UNAVAILABLE and retrieval falls back to "
            "router+hybrid (never break boot, NON_NEGOTIABLE 15). If scripts/upstream_lock.py --check demands a "
            "clone for every upstream, teach it a model pin (repo, revision, file sha256) rather than cloning "
            "weights. Retrieval: dense = bge cosine over each tool's L1 text (cache the document vectors "
            "keyed by the catalogue's hash), fused with router/BM25, then the cross-encoder reranks the top "
            "k (k <= 20). Measure Recall@1/@3/@5, per-request latency and schema tokens on the dev split with "
            "scripts/bench_tool_disclosure.py and FRIDAY_MODELS_DIR=F:/F.R.I.D.A.Y/cache/models; target "
            "Recall@1 >= 0.90 and Recall@3 >= 0.97 with the 80% disclosure reduction kept; read the holdout "
            "once, only after dev passes. Unit tests must not need the model files (a fake session); a "
            "model-backed test skips with a stated reason when the files are absent. If the models still "
            "miss the target, report the measured numbers and the ambiguous-label cases as a gap; do not "
            "relabel the dev split."),
    }),
    # --- owner rulings on the three blocked P0 rows, 2026-10-03 (00-owner-directive-2026-10-03.md)
    ('SEC-005', {
        "status": PARTIAL,
        "next_action": (
            "RULING 2026-10-03 (owner): enforce the filesystem boundary; advisory-only file roots are NOT "
            "accepted under HOST. Enforce it below Hermes rather than inside it: every worker session gets a "
            "runtime-scoped filesystem envelope, and the only file access a Hermes delegation has is FRIDAY's "
            "jail-backed file capability - where Hermes cannot enforce the envelope itself, remove or bypass "
            "its unrestricted file tools for that delegation (do not edit Hermes' internals under D:/hermes). "
            "Path: worker session -> execution envelope (the delegation's write and read roots) -> FRIDAY file "
            "capability (friday/toolsets/files.py, friday/fsjail.py) -> canonicalize the path -> resolve "
            "symlink, junction and reparse-point targets -> allowed-root check -> read or write; refused "
            "otherwise, and the refusal is logged. The blocker the last run found stands as the seam to "
            "design around: the exec bridge is spawned once per Hermes profile with a fixed env and no session "
            "id (execution_bridge.py:57-74), so the per-session root must reach the bridge another way (per "
            "session bridge, or a session-scoped root passed with each call and checked by FRIDAY). Windows "
            "tests, each a refusal or an allowed access that fails without the change: ordinary paths, '..', "
            "symlinks and junctions out of a root, UNC paths, case variations, forward and back slash separators, "
            "and redirected known folders. A prompt telling the worker to stay in a directory is not "
            "isolation and does not count."),
    }),
    ('CTX-003', {
        "status": PARTIAL,
        "next_action": (
            "RULING 2026-10-03 (owner): the objective engine hands work to the context compiler, never "
            "straight to a model or a worker. Boundary: Objective Engine -> Objective Contract -> WorkPacket "
            "/ ContextRequest -> Context Compiler (model_gateway.compile_working_set, CTX-001: CriticalFactSet, "
            "literals, authority and policy, L0/L1/L2 retrieval, compaction, provenance) -> Model Gateway / "
            "worker (DevelopmentRun, Hermes task bundle). CriticalFactSet belongs to the Objective Contract "
            "side and is an input constraint on compaction: the compiler may shorten representation but may "
            "never silently alter or omit critical facts, exact literals, authority state, acceptance "
            "criteria, active blockers or required provenance - an over-budget package that cannot keep them "
            "fails loudly rather than dropping them. So: add the production caller (the objective engine "
            "emits a WorkPacket/ContextRequest; development.for_goal is reached through the compiler with "
            "store= and objective_id=), and give the checkpoint summary no size cut that can lose a fact - "
            "any bound goes through the compiler's fact-preserving compaction and reports what it reduced. "
            "The objective engine must not become a prompt builder itself. Tests: a critical fact, a literal "
            "and an active blocker survive compaction under a tight budget; a package that cannot fit them "
            "fails; the objective path reaches the worker only through the compiler."),
    }),
    ('MCP-001', {
        "status": PARTIAL,
        "next_action": (
            "RULING 2026-10-03 (owner): pin identity, but no mandatory leaf-certificate pinning for HTTPS MCP "
            "servers (routine certificate rotation would become an outage). FRIDAY-managed local stdio "
            "servers: pin the executable / package / source version, the command, its hash, the server's "
            "metadata and a tool-schema fingerprint. Remote servers: pin the registered origin / endpoint, "
            "the authenticated server relationship, the advertised server identity, protocol compatibility "
            "and tool and description fingerprints; SPKI (public-key) pinning is optional, only for endpoints "
            "FRIDAY controls or that need the stronger binding. Servers configured outside FRIDAY (Claude "
            "Code, Hermes, other apps' configs) enter as EXTERNAL_DISCOVERED -> EXTERNAL_UNVERIFIED -> "
            "fingerprint + scan + schema capture -> user/admin approval where required -> ACTIVE, and are "
            "never callable merely because another tool's config knows them. Any unexpected change of "
            "endpoint/origin, server identity, executable/hash, tool schema, tool description, permissions "
            "or auth scope moves ACTIVE -> SUSPENDED -> REVALIDATE (friday/mcp_firewall.py already suspends "
            "on a handshake name/version change; extend it to these). Tests per transition and per drifted "
            "field, a refused call for an unverified external server, and no TLS leaf pin required."),
    }),
    ('APPROVAL-001', {
        "status": PARTIAL,
        "current_implementation": 'Approval lease now binds blast_radius (capability side_effect+risk x target count x whose data). Recorded on grant; runtime refuses a yes whose current blast radius differs or is absent. 4 new tests. (build run, commit b021e636b)',
        "next_action": "GAPS: CAP-004's six effect flags are not declared yet; blast radius uses the existing side_effect and risk fields. Swap in the flags when CAP-004 lands.; Full 105-file impact set not run; 9 related files were.",
    }),
    # --- CTX-002: owner ruling for a staged router, 2026-10-03 (supersedes 2026-10-02 h's "do not relabel")
    ('CTX-002', {
        "status": PARTIAL,
        "next_action": (
            "RULING 2026-10-03b (owner, docs/plans/build-harness/00-owner-directive-2026-10-03b.md): build a "
            "staged router - better candidate recall (a larger embedder) AND a reasoning model that chooses "
            "among the top k only when retrieval is uncertain - not either alone, and not a large model on "
            "every request. START: `git checkout wip/ctx-002-models -- friday/tool_retrieval.py "
            "friday/tool_models.py friday/fabric_adapters/tool_embedder.py friday/fabric_adapters/tool_reranker.py "
            "scripts/bench_tool_disclosure.py scripts/upstream_lock.py third_party/UPSTREAM_LOCK.json "
            "tests/test_tool_retrieval.py tests/test_tool_models.py tests/test_upstream_lock.py` (run 2: pinned "
            "bge-small embedder and MiniLM reranker as fabric providers, hash-verified offline loading with "
            "fallback, model rows in the upstream lock, the benchmark). Run 2 measured on dev (R@1/@3/@5): "
            "router 0.369/0.621/0.704, bge-small 0.401/0.656/0.748, router+BM25+bge 0.414/0.677/0.749 at 25 ms, "
            "+reranker 0.368/0.548/0.639 at 1.5 s. STEPS: (1) Adjudicate ambiguous labels first: write the "
            "adjudication rule before measuring; keep every original label and add also_correct tools or an "
            "expect=CLARIFY marker with a one-line reason; never take a label from a model's output; apply the "
            "same written rule to the holdout without looking at any model result on it. (2) Baseline = the "
            "router+BM25+embedding hybrid. Drop the MiniLM reranker from routing (it lowered recall and cost "
            "1.5 s). (3) Benchmark bge-small against BAAI/bge-base-en-v1.5 (MIT, revision "
            "a5beb1e3e68b9ab74eb54cfd186867f64f240e1a, onnx/model.onnx sha256 "
            "9bc579acdba21c253c62a9bf866891355a63ffa3442b52c8a37d75b2ccb91848, tokenizer.json sha256 "
            "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66; already downloaded under "
            "F:/F.R.I.D.A.Y/cache/models) on the same data, hybrid weights and tool text. Only if base wins "
            "materially, fetch BAAI/bge-large-en-v1.5 by pin (MIT, revision "
            "d4aa6901d3a41ba39fb536a557fa166f842b0e09, onnx/model.onnx 1.34 GB sha256 "
            "69ed3f810d3b6d13f70dff9ca89966f39c0a0e877fb88211be7bcc070df2a2ce) and measure it too, after "
            "checking free memory. Report Success@1/3/5/10/20, MRR, latency and peak RAM per model; promote a "
            "larger model only for a material win on FRIDAY's benchmark, never for being larger. (4) Enrich the "
            "tool cards the retriever indexes: does, does_not, aliases, examples, confusable_with, domain - "
            "written from the tools' behaviour, never copied from dev or holdout requests (that leaks the test "
            "set); re-measure. (5) Stage 2: when retrieval is confident (top-1 score and margin to top-2 over "
            "thresholds chosen on dev) route directly, about 25 ms and no model call; otherwise send the request "
            "with compact cards for the top 10 (try 20 if coverage@10 is short) to a reasoning model through "
            "FRIDAY's model gateway, which may return only {decision: TOOL | CLARIFY | ABSTAIN, tool_id, "
            "confidence}. Measure end to end: correct tool, correct clarification, correct abstention, and the "
            "share of requests that need stage 2. No paid API call from the benchmark (the owner's day budget "
            "counts every paid call): on 2026-10-02 the OpenAI key here was invalid (401 in "
            "tests/test_voice_path.py) and the exe's Gemini key was rejected, so build stage 2 with a fake "
            "chooser in tests and list the live stage-2 measurement as a gap. FINISH as DONE with "
            "acceptance_met=false and the gaps listed, so the work integrates as PARTIAL; BLOCKED is only for a "
            "decision only the owner can make. CTX-002 stays PARTIAL until the held-out metrics pass."),
    }),
    ('CTX-003', {
        "status": PARTIAL,
        "current_implementation": 'Objective engine reaches workers only through the context compiler (compile_request / development.for_request); critical facts, literals, blockers and now acceptance criteria survive tight budgets or the package fails loudly. (build run, commit d2ae3bd28)',
        "next_action": "GAPS: No production capability calls development.for_request yet. for_goal had no production caller before this change either; registering one needs an owner decision on its policy category (what authority starting a development run in a repo carries) and a capability_router group. The seam is enforced by tests/test_context_compiler.py (for_goal is called only from for_request, which compiles first), and the live objective path that does reach a worker today (hermes_delegate) goes through mg.compile_request.; tests/test_model_gateway.py::test_live_hermes_gateway_answers_without_an_agent_loop is @pytest.mark.live; it trips the TEST-ISOLATION-001 guard on data/gateway_calls_live_test.sqlite3 when run without the gate's -m 'not live' filter. It needs a live Hermes install, is excluded from the gate, and is outside this change.; Full 4-chunk baseline suite not run in this worktree (host is memory-constrained); only the targeted files above plus the other for_objective users were run.",
    }),
    # --- SEC-005: owner ruling on commands in scoped delegations, 2026-10-03 (c)
    ('SEC-005', {
        "status": PARTIAL,
        "next_action": (
            "RULING 2026-10-03c (owner, docs/plans/build-harness/00-owner-directive-2026-10-03c.md): merge SEC-005 "
            "with command execution DISABLED for scoped delegations. The verifier failed build/sec-005 (3d9654f) "
            "because bridge_run_command and interpreters (python, node) stayed available during an envelope-bound "
            "delegation and the refusal message invited them, so the file envelope was advisory for commands. Fix: "
            "while a delegation scope is registered, refuse bridge_run_command outright (no interpreter or "
            "executable allowlist), say so in the refusal without suggesting any interpreter, and report the "
            "commands part of bounded_by as DISABLED, not ADVISORY. Tests: a scoped delegation's bridge_run_command "
            "is refused, including a python or node command that would write outside the envelope (assert the file "
            "is not created); unscoped behaviour is unchanged; the existing 176 tests stay green. Commands come back "
            "only through EXEC-SBX-001 (AppContainer/LPAC or Windows Sandbox, after real escape tests)."),
    }),
    ('MCP-001', {
        "status": PARTIAL,
        "current_implementation": 'Firewall pins endpoint, protocol, relationship, permissions, auth scope, metadata, stdio exe hash/command/package; any drift suspends. External servers go DISCOVERED->UNVERIFIED->approve->ACTIVE; calls refused at the client until then. No TLS leaf pin; SPKI optional. (build run, commit b27e5c21d)',
        "next_action": "GAPS: No live caller connects to an external server yet: external=True, discover_config (mcpServers JSON shape only; no Hermes config reader) and stdio_binding are exercised by tests and the approve CLI, not by a production path.; Approval is always required for an external server; no admin policy that waives it ('where required') exists.; protocol is pinned as a caller-registered revision, not the negotiated one (a client upgrade renegotiates); the voice agent pins the endpoint only.; tests/test_prd_ledger.py::test_the_real_ledgers_are_clean fails in this worktree on 7 cited data/ paths absent here (CORE-002, CTX-002, RES-001/2/3, SCOUT-001); none is MCP-001. scripts/prd_ledger.py MCP-001 line citations were not refreshed.",
    }),
    ('TRAJ-001', {
        "status": PARTIAL,
        "current_implementation": 'Root cause fixed in code: the trajectory check ran only after a step had started, so a first batch of side-effecting steps under an open contract question ran to COMPLETED with nobody asked (reproduced: 3 independent files_write steps, status COMPLETED). trajectory.assess now takes `upcoming` (the batch about to dispatch) and flags a side-effecting step that is about to start under an open question; the executor passes ready[:remaining portion] before _execute_tasks, so the pause happens before any step starts or dispatches. A resume after a flag that named the questions is treated as carry on, so the same questions are not asked again (otherwise carry on would re-pause forever). Attempt 3 wrote status PARTIAL, which the kernel turns into FAILED, so its work was never committed; attempt 4 re-verified it unchanged apart from one type annotation (continuous._trajectory_pauses `upcoming` is now Sequence[str], matching its () default). Attempt 3 reported the new tests as 30 failed / 138 passed before the source change; attempt 4 re-ran the suites after it: 205 passed. (build run, commit 23e9d3325)',
        "next_action": "GAPS: user-amendment and worker-conflict signals: no amendment event exists yet. A question added after the person said carry on is asked again (tested), but the question count is the only identity, so replacing one open question with another of the same count would not be re-asked; needs an amendment event, which does not exist; non-pause actions (fresh worker, stronger model, lower concurrency): executor still only pauses and asks; owner decision on routing policy needed; forbidden-class and max_side_effects contract signals are covered by synthetic traces only: compile and dispatch already refuse such steps, so no executor-level path reaches them. They are not checked pre-dispatch, deliberately; the watchdog path _drive_one_round does not run the trajectory check at all (unchanged, out of scope); a run driven only by it is not assessed; ASSUMPTION, owner confirmation wanted: a resume after a flag naming the open questions waives those questions for the rest of the run. No ruling was found in the PRD or NON_NEGOTIABLES; the alternative (ask again each window) makes carry on mean one batch. Revisit if wrong; the waiver is lost on a long run: trajectory.for_run reads store.recent_objective_events, which returns only the newest 500 events of the run (friday/store.py:3218). The waiver looks for the flag in the events before the last resume (trajectory.py:176), so once 500 or more events follow that resume, the resume and the flag fall out of the window, `waved_through` becomes False, and the same open questions are asked again. Not fixed here and not tested; a fix needs the flag and resume read independently of the window (or a durable acknowledgement marker), which is a store seam change; tests/test_prd_ledger.py::test_the_real_ledgers_are_clean cannot pass in this worktree: the data/ files it cites are untracked and live only in the main checkout (needs a live checkout, not a change here); docs/golden/objectives.jsonl shows a working-copy line-ending-only change (empty content diff) written by a golden suite during attempt 3's neighbour run; it is left unstaged and is not part of this change",
    }),
    ('MCP-001', {
        "status": PARTIAL,
        "current_implementation": 'Acceptance already held: an unexpected MCP schema change suspends the server (47 firewall tests green). This run refreshed the stale MCP-001 file:line citations and added a test that fails when they drift. Remaining gaps need owner rulings. (build run, commit 3ee481d36)',
        "next_action": "GAPS: No live caller connects to an external server: external=True, discover_config (mcpServers JSON only, no Hermes config reader) and stdio_binding run only in tests and the approve CLI. Needs the Hermes config location/format.; Approval is always required for an external server; no admin policy waives it ('where required' is undefined). Needs an owner ruling on who may waive and when.; protocol is pinned as the caller-registered revision, not the negotiated one; pinning the negotiated one would suspend the server on every client library upgrade. The voice agent pins the endpoint only. Needs an owner ruling.; tests/test_prd_ledger.py::test_the_real_ledgers_are_clean still fails here on 7 cited data/ paths absent from this worktree (CORE-002, CTX-002, RES-001/2/3, SCOUT-001); none is MCP-001. The 1 failure in the run above is this test.",
    }),
    # --- CTX-003: owner ruling on the authority to start a development run, 2026-10-03 (d)
    ('CTX-003', {
        "status": PARTIAL,
        "next_action": (
            "RULING 2026-10-03d (owner, docs/plans/build-harness/00-owner-directive-2026-10-03d.md): register the "
            "production capability that starts a development run (it calls development.for_request, so the context "
            "compiler stays the only way to a worker). It is a selfdev/FILE_WRITE-class action in the 'selfdev' "
            "router group (beside selfdev_run): it asks by default, and proceeds without another prompt only when "
            "APPROVAL-001's lease (objectives / capability_runtime) is valid and explicitly covers development work "
            "for that workspace/repo and blast radius. The run stays confined to its own sandbox worktree. Merging "
            "or promoting into a live branch is a separate capability at a higher authority (COMMAND_EXECUTION) that "
            "needs fresh approval unless the lease explicitly names promotion to that exact branch. Until "
            "EXEC-SBX-001 exists, a development run that needs command execution is refused with that reason - "
            "never a fallback to unrestricted host commands. Tests: a covering lease starts the run with no prompt; "
            "no lease, another repo or a different blast radius asks; promotion without an exact-branch lease asks; "
            "a run that needs commands is refused citing EXEC-SBX-001; the seam test still proves the objective path "
            "reaches the worker only through the compiler."),
    }),
    # --- MCP-001: owner rulings on discovery, approval waivers and protocol pinning, 2026-10-03 (e)
    ('MCP-001', {
        "status": PARTIAL,
        "next_action": (
            "RULINGS 2026-10-03e (owner, docs/plans/build-harness/00-owner-directive-2026-10-03e.md). "
            "(1) Discovery: Hermes' canonical live MCP config is $HERMES_HOME/config.yaml under the YAML key "
            "mcp_servers; HERMES_HOME defaults to ~/.hermes (C:/Users/<user>/.hermes on Windows) and a named "
            "profile lives at ~/.hermes/profiles/<name>/config.yaml. Read that, beside FRIDAY's managed registry; keep "
            "the JSON mcpServers parser (Claude Code) and Codex's config only as optional external/import discovery, "
            "and do not treat a profile distribution's mcp.json as the live source. Everything found outside FRIDAY "
            "enters as EXTERNAL_DISCOVERED. (2) Approval: lifecycle EXTERNAL_DISCOVERED -> EXTERNAL_UNVERIFIED -> "
            "VERIFIED_PENDING_APPROVAL -> ACTIVE. First activation of an external server needs explicit owner/admin "
            "approval unless a standing approval lease explicitly covers that exact server or trust class; a waiver "
            "can come only from the owner, a workspace administrator policy, or such a lease - a model can never "
            "waive. An approved, verified server (or a FRIDAY-managed one) reconnects and restarts with no new prompt "
            "while its server identity, transport class, origin/executable identity, permission/risk class, "
            "credential scope, tool authority ceiling, approved workspace and trust-policy version are unchanged; "
            "ordinary non-authority metadata changes do not prompt. Any authority or identity change: SUSPENDED -> "
            "REVALIDATE -> approval if needed. (3) Protocol: never pin the last negotiated version as identity. "
            "Persist the policy (allowed eras legacy/modern, allowed versions, e.g. 2025-11-25 and 2026-07-28), the "
            "registered preference (e.g. auto) and the last observed version and era. A compatible change of "
            "negotiated version is recorded and re-runs the semantic health/schema checks, no suspension; suspend only "
            "on a version outside the policy, an unapproved legacy<->modern lifecycle change, an identity, endpoint or "
            "executable/hash change, a tool gaining higher authority, a material schema change, an expanded "
            "credential/auth scope, or a change in security-sensitive capabilities. friday/mcp_firewall.py currently "
            "suspends on a handshake name/version change: keep the identity half, replace the version half with the "
            "policy. Tests per lifecycle transition, per waiver source (and a model-issued waiver refused), the silent "
            "reconnect and each listed suspension trigger, a compatible SDK version bump that does not suspend, and "
            "discovery from a fixture config.yaml for the default and a named profile."),
    }),
    ('CTX-002', {
        "status": PARTIAL,
        "current_implementation": 'Staged router built and measured on dev: hybrid+cards+bge-small S@1/5/10 .450/.764/.858 vs router .381/.714/.825. bge-base not material, large skipped. No confidence signal routes directly at 95%, so all requests go to stage 2 (fake chooser only). (build run, commit 0632b4c18)',
        "next_action": 'GAPS: Live stage-2 accuracy is unmeasured: the benchmark makes no paid call (OpenAI key invalid 2026-10-02, Gemini key rejected); GatewayChooser is tested with a fake chooser only.; Direct routing is off: at the 95% precision target no (agree, margin) cell routes anything, and score-based signals (cosine top-1, gap, agreement) reach only 66% precision at 13% routed. Top-1 is right on 45% of dev rows, so every request currently needs stage 2. A better confidence signal or a measured chooser is needed before loosening Thresholds.; Stage-2 candidate coverage is 85.8% at top 10 and 93.2% at top 20; a larger k should be measured with the live chooser.; Held-out metrics were not measured (holdout deliberately not read); CTX-002 stays PARTIAL until they pass.; Label adjudication covers the stated substitution sets and bare-pronoun rows only; no row-by-row human review.; scripts/upstream_lock.py --check reports drift in this worktree because the third_party clone dirs are empty here (46 clone rows differ; the 3 model rows match derive()); re-run it in the main checkout.; bge-large not fetched: bge-base was not a material win over bge-small (S@5 +1.5, S@10 +1.1, S@1 -0.6 points for 1.6x latency, 2.2x RAM).; implementer did not claim acceptance met',
    }),
    ('SEC-005', {
        "status": PARTIAL,
        "current_implementation": "Owner ruling 2026-10-03c applied: while any delegation holds the exec bridge under an envelope, bridge_run_command is refused outright (delegation_scope.refuse_commands, called before the command is parsed, no executable allowlist, refusal names no program), and bounded_by reports commands as DISABLED (new contracts.DISABLED), not ADVISORY. A python/node/git/powershell command from a scoped delegation is refused and an outside file is not created; file access stays FRIDAY's jail-backed read/write/list/search held to the envelope roots. Unscoped calls are unchanged. Checked in-process and through a real stdio bridge process. (build run, commit 33e52c189)",
        "next_action": "GAPS: Not run against a live Hermes gateway under D:/hermes (needs the owner's host): the bridge was driven as its own stdio MCP process (python, node, git and powershell commands refused under a registered scope, outside file never created, in-envelope read/write allowed, outside write refused, commands back after release), but no Hermes model turn was run through it.; The bridge cannot tell callers apart (no session id), so while a delegation holds it no command runs for any caller on that Hermes profile, and a command already running when a delegation registers (up to the 600 s cap) is not stopped. Commands return for delegations only through EXEC-SBX-001 (AppContainer/LPAC or Windows Sandbox, after real escape tests).; tests/test_prd_ledger.py::test_the_real_ledgers_are_clean fails in this worktree on 7 missing untracked data/ evidence paths of other rows; not caused by this change, not run in a tree that has data/. scripts/prd_ledger.py was not edited: its SEC-005 next_action ('EXTEND: Hermes file roots and network are ADVISORY ...') is now stale and is the kernel's to update.; docs/plans/build-harness/00-owner-directive-2026-10-03c.md is not present in this worktree; the ruling was taken from the text quoted in .build/packet.md.; Network scope and tool-call limits stay ADVISORY for a Hermes delegation (unchanged; the ruling concerns file access and commands).; A delegation workspace outside FRIDAY's own jail roots (repo, data workspace/artifacts, Documents, Desktop, Downloads) gets no file access: the jail is the outer limit and the envelope only narrows it. Fails closed; the owner may want those roots widened for D:/ project workspaces.; The bridge serves one workspace at a time: a second concurrent delegation under a different workspace is refused at registration.; Hermes' native file/terminal toolsets are detected from the profile config.yaml only; an envelope-bound delegation is refused when they are on or the config is unreadable. A runtime change to the profile after the check is not seen.",
    }),
    # --- EXEC-SBX-001: three sandbox tiers with NVIDIA OpenShell as K2, owner 2026-10-03 (g)
    ('EXEC-SBX-001', {
        "status": MISSING,
        "next_action": (
            "RULINGS 2026-10-03c and 2026-10-03g (owner; docs/plans/build-harness/00-owner-directive-2026-10-03c.md "
            "and -03g.md). Three tiers behind one scoped command runner: K1 native AppContainer/LPAC for lightweight "
            "Windows jobs; K2 NVIDIA OpenShell through WSL2 for coding, build and test workers (a policy-enforced agent "
            "runtime: gateway plus sandbox supervisors enforcing filesystem, process, network, credential and inference "
            "boundaries, Landlock/seccomp and a policy proxy on Linux, endpoint-bound credential injection so a "
            "sandbox never holds raw secrets; Windows support is through WSL2 and experimental); K3 Windows Sandbox or "
            "a VM for highly untrusted work. Job Objects only for process-tree and resource control. Start with K2: "
            "evaluate OpenShell through the capability fabric (read its LICENCE, pin the commit, adapter module under "
            "friday/fabric_adapters/, an absent install reports UNAVAILABLE), since it may spare FRIDAY building most of "
            "the K2 infrastructure; OpenShell wraps execution only and never replaces FRIDAY's objective engine, "
            "policy engine, model router or UI. WSL is present on this host (C:/Windows/System32/wsl.exe). "
            "bridge_run_command and interpreters return to scoped delegations only after real escape tests pass: "
            "Python, Node, PowerShell and child processes cannot read or write outside the ExecutionEnvelope by "
            "traversal, absolute, UNC or network paths, case variations, symlinks or junctions. The same sandbox is "
            "the preferred runtime for the Capability Marketplace's dynamic evaluation (CAP-MKT-003)."),
    }),
    # --- CTX-002: owner rulings 2026-10-04 (key, holdout, flag + shadow)
    ('CTX-002', {
        "status": PARTIAL,
        "next_action": (
            "RULINGS 2026-10-04 (docs/plans/build-harness/00-owner-directive-2026-10-04.md). (1) No raw API key: the stage-2 "
            "chooser runs through a coding-agent CLI (Claude Code `claude -p`, Codex or Antigravity) via the model gateway, "
            "on a bounded dev sample, each call's cost recorded in the day ledger. (2) Measure the sealed holdout exactly "
            "once, after the chooser and thresholds are fixed on dev; never tune on it. (3) Wire the staged router into "
            "capability_router search behind tool_router_mode = legacy | shadow | staged (default legacy; plan under "
            "docs/plans/ctx-002/). shadow: legacy decides, the staged router also runs and logs legacy choice, staged top-k, "
            "confidence, latency, would-clarify and the observed successful tool, with no effect on execution; stage 2 only "
            "on a bounded sample or a configured evaluator. staged: owns routing with automatic fallback to legacy on error "
            "or timeout. Promotion to staged needs held-out and shadow evidence: Success@1 >= 0.90, Success@3 >= 0.97, "
            "ambiguous-intent handling, acceptable p95 latency and chooser invocation rate, no golden-journey regression. "
            "Tests: each mode, the fallback, that shadow never changes the executed tool, the invocation-rate metric."),
    }),
    ('CTX-003', {
        "status": PARTIAL,
        "current_implementation": 'Registered development_run (FILE_WRITE, selfdev group; only caller of development.for_request) and development_promote (COMMAND_EXECUTION). Both ask unless an APPROVAL-001 lease covers them; commands refused citing EXEC-SBX-001. 16 new tests plus seam pin. (build run, commit 67dc6b573)',
        "next_action": 'GAPS: development_run stages the run (facts compiled, repo mapped, team chosen) but does not execute a worker: choosing/building an executor is not wired, and any command-running worker waits on EXEC-SBX-001.; needs_commands is declared by the caller; below it, delegation_scope.refuse_commands (SEC-005) is what stops a worker running commands.; Lease coverage uses the existing APPROVAL-001 per-action record (same capability, arguments, plan, blast radius); no broader repo-wide lease format was added.; Wider sweep of 94 impacted test files: only failures were test_phase1b live-browser (@live, excluded from gate) and test_prd_ledger::test_the_real_ledgers_are_clean (data/prd_v7_requirements.json absent in this worktree; not touched).; docs/golden/objectives.jsonl shows modified in git status after the suite ran (line-ending only, no content diff); not part of this change.',
    }),
    ('MCP-001', {
        "status": PARTIAL,
        "current_implementation": "Closed the legacy-pin hole in _offer: a handshake whose sensitive capability set is empty (e.g. only logging) dropped the capabilities key, which _reconnect reads as a pin made before capabilities were recorded, so a later prompts/resources/sampling/experimental declaration was silently adopted. An empty list is now stored as a pinned value, so the same change suspends with 'capabilities: [] -> [...]'. 8 regression cases (4 capabilities x binding/no-binding) failed first with KeyError, pass now. MCP-001 next_action text refreshed. The friday/mcp_firewall.py line count is unchanged, so the ledger citations still land. (build run, commit 9eeae9784)",
        "next_action": 'GAPS: tests/test_prd_ledger.py::test_the_real_ledgers_are_clean fails in this worktree (103 passed, 1 failed across the two files): 7 problems on CORE-002, CTX-002, RES-001 (x2), SCOUT-001, RES-002, RES-003 cite data/ files that are not present here. None is MCP-001 (PL.check(v1) is clean); data/ is off limits; needs the main checkout\'s data/ to confirm green.; A pin made from a handshake but with no binding now stores {"capabilities": []}. If a binding with an endpoint arrives later it suspends with \'transport class: None -> remote\' (observed with a throwaway script, not pinned by a test). Pins of [\'tools\'] already behave this way.; No live path calls Firewall.inspect for a server that Hermes or Claude Code workers configure, so the external lifecycle (discover, verify, approve, waive) runs only from the CLI and tests. It has not been exercised against a live Hermes; that needs a live run outside this worktree.; Assumption, revisit if wrong: server metadata (the title and instructions a server hands the model) is treated as authority-bearing, so a change suspends. The owner ruling that ordinary non-authority metadata does not prompt could be read to exclude it; tests/test_mcp_firewall.py:204 pins the current reading.; The voice-agent wiring (agent_friday.py:739, 1328) was not changed and was not re-run against a live LiveKit session.',
    }),
    # --- CTX-002: slice the 2026-10-04 rulings so each run (and its verifier) has one scope
    ('CTX-002', {
        "status": PARTIAL,
        "next_action": (
            "SLICED 2026-10-04 (controller; owner rulings in docs/plans/build-harness/00-owner-directive-2026-10-04.md). "
            "CURRENT SLICE 1 - wiring only: tool_router_mode legacy|shadow|staged (default legacy), shadow logs and never "
            "changes the executed tool, staged falls back to legacy on error or timeout, and staged acts on ABSTAIN (falls "
            "back to legacy) and CLARIFY (asks) with agent-level tests. Benchmark code that spends money or opens the sealed "
            "holdout (LedgeredChooser, chooser_benchmark, frozen_run) is either tested with fakes or left out of slice 1. "
            "Slice 1 integrates as PARTIAL; measured gates are NOT its acceptance. LATER SLICES (not in slice 1): 2 - "
            "measure the stage-2 chooser through a coding-agent CLI on a bounded dev sample, each call in the day ledger; "
            "3 - the one-time holdout measurement after dev is fixed; 4 - promotion to staged when Success@1 >= 0.90, "
            "Success@3 >= 0.97, ambiguity handling, p95 latency, chooser invocation rate and golden journeys pass."),
    }),
    ('CTX-002', {
        "status": PARTIAL,
        "current_implementation": "Five test stand-ins gain note_turn so turns no longer crash on the router's new method. Agent-level tests prove ABSTAIN serves legacy with no question, and shadow mode leaves the answer and executed tool unchanged. MCP-001 ledger cite agent_friday.py:1328 moved to 1330. (build run, commit a2d9cf40d)",
        "next_action": "GAPS: Staged search runs synchronously and can hold up search_capabilities (agent_friday.py:942) for up to timeout_s, 2 s by default.; Slices 2-4 are still open.; CLARIFY depends on the model following the instruction, which a unit test cannot check.; upstream_lock --check was not run because the clones are empty.; tests/test_prd_ledger.py::test_the_real_ledgers_are_clean fails in this worktree on 7 cited data/ files that are absent here (CORE-002, CTX-002, RES-001 x2, SCOUT-001, RES-002, RES-003); it is not in the required command and none of the 7 is a cite this change touched.; Items 1-5 were not re-run against ba44fac to watch them fail first; the packet's root cause was taken as given. Items 6-7 were mutation-checked: ABSTAIN returning the staged shortlist, and shadow serving the staged answer, each fail the new tests.; implementer did not claim acceptance met",
    }),
    ('BROW-004', {
        "status": PARTIAL,
        "current_implementation": "Browser tools (open/act/automate) now return UNSUPPORTED naming the probe's typed state and reason when Chromium or Playwright cannot start, via a shared classify_failure; a failed launch also releases its driver. Probe here: BROWSER_READY 1.7s, 283 MB. (build run, commit e597f5270)",
        "next_action": "GAPS: REFERENCE-B has not run the probe; it needs that machine (this machine: BROWSER_READY, 1.7 s, 283.3 MB, Playwright 1.59.0); friday/ui_browser.py (control-room browser) still reports a boot failure as a bare RuntimeError, not the probe's typed reason; tests/test_phase1b.py::test_live_browser_opens_and_closes (live) fails on page text: example.com body lacked 'Example Domain' in the run; the browser launched and opened fine, so not caused by this change; contracts.ActionResult.honest_summary has no NOT_CONFIGURED branch (falls through to 'still working'); UNSUPPORTED was used because its summary reads 'not available on this machine'; implementer did not claim acceptance met",
    }),
    ('ASSUME-001', {
        "status": PARTIAL,
        "current_implementation": 'assumption_register (added in 1e0811d) was in neither workspace.SCOPED nor UNSCOPED, failing test_every_table_is_declared. Declared it UNSCOPED as child rows of objective_runs, like objective_tasks/events/deliveries. (build run, commit 14dc8244b)',
        "next_action": "GAPS: 'High-impact' threshold (consequence HIGH, or LOW confidence with MEDIUM+ consequence) is an assumption; owner to confirm.; Model-emitted confidence/consequence for SMALL questions was only tested with a stub, never live.; Full four-chunk baseline_suite.py was not run; only the six test files named in the packet were re-run for this fix.; The 'failed before' state of test_every_table_is_declared was established by reading, not by a pre-edit run: grep showed assumption_register only in friday/store.py, so it was undeclared before the edit.",
    }),
    ('BROW-004', {
        "status": PARTIAL,
        "current_implementation": "Control-room browser (ui_browser) now reports a failed boot as the probe's typed state (BrowserUnavailable via classify_failure), fails fast instead of a 45 s hang, frees its driver, and retries on the next call; open_url/study_url return status 'unavailable' with the reason. (build run, commit 1c832b477)",
        "next_action": "GAPS: REFERENCE-B has not run the probe; it needs that machine (this machine: BROWSER_READY, 1.7 s, 283.3 MB, Playwright 1.59.0); tests/test_phase1b.py::test_live_browser_opens_and_closes (live) failed on page text in the earlier run (example.com body lacked 'Example Domain'); not re-run here, not caused by this change; contracts.ActionResult.honest_summary has no NOT_CONFIGURED branch (falls through to 'still working'); UNSUPPORTED is used because its summary reads 'not available on this machine'; voice_brain/ui_server callers of ui_browser.open_url show a generic 'I couldn't open' for status 'unavailable'; the typed reason is in the returned dict, not yet spoken or shown; the new ui_browser tests use a faked Playwright, so a real control-room launch failure was not exercised; implementer did not claim acceptance met",
    }),
    ('BBOX-001', {
        "status": PARTIAL,
        "current_implementation": "observability.reconstruct() replays a run's outcome and task statuses from the event log and verifies the audit chain, then names every disagreement with the ledger rows. Reachable via objective_trace(blackbox=True). 4 new tests + 1 extended. (build run, commit 91e5f53b6)",
        "next_action": 'GAPS: No new capability/tool id: the replay is a flag on objective_trace, so the capability description, tool card and intent examples still describe only the timeline trace.; reconstruct reads at most 5000 events; a longer log is reported as a partial replay, not paged.',
    }),
    ('CAP-003', {
        "status": PARTIAL,
        "current_implementation": 'Capability record gains version, backend, provenance, permissions, data_classes, os_support, latency/cost class, tests, last_verified (validated). capabilities.health() is probe-backed: no probe = REGISTERED, failing probe = UNAVAILABLE, never READY by default. (build run, commit 52e9c0bb3)',
        "next_action": 'GAPS: No native capability has a probe registered yet; all report REGISTERED until one calls capabilities.register_probe.; Registry fields default to undeclared; the 160+ existing entries are not back-filled with version/os/latency/tests.; Assumed vocabularies for provenance, data_classes, os_support and latency_class (no documented source); revisit if wrong.; fabric.health still reports READY for an adapter with no probe; out of scope for this packet.',
    }),
    ('CLI-001', {
        "status": PARTIAL,
        "current_implementation": 'Same TaskBundle now runs through the real ClaudeCodeExecutor and HermesExecutor (real bridge, fake gateway). A new friday/executors/work_packet.py maps each terminal ActionResult to the PRD s46 eight-field output; both pass, and a failed run still yields every field. (build run, commit 47ea91e24)',
        "next_action": 'GAPS: PRD s46 output contract existed only in docs/PRD_V7.md; no code emitted it. work_packet.output_of is my mapping of ActionResult to the 8 fields (assumption: lives in friday/executors/, owned by objective-engine). Owner should confirm the mapping.; output_of has no production caller yet: nothing on the manager-facing path (e.g. DevelopmentRun reporting) consumes it. Wiring that is a separate change.; Hermes adapter reports no changed files, and no executor runs tests, so files_changed is [] for Hermes and tests is always []; remaining_risk says so rather than guessing.; Fail-before is by import: the new tests import friday.executors.work_packet, which did not exist. Not run against the old tree.; Read budget (<=400 lines before first edit) was exceeded: about 800 lines read.',
    }),
    ('CLI-001', {
        "status": PARTIAL,
        "current_implementation": 'DevelopmentRun.execute now records work_packet.output_of(result) and report() exposes it, so the manager sees the same PRD s46 eight fields from Claude Code or Hermes. Tests drive both adapters through a run. (build run, commit 2048ad98c)',
        "next_action": "GAPS: Owner should still confirm the ActionResult -> s46 field mapping in work_packet.output_of (assumed it lives in friday/executors/, objective-engine).; Hermes reports no changed files and no executor runs tests, so files_changed is [] for Hermes and tests is always []; remaining_risk says so.; A result that is not a contracts.ActionResult (loose test fakes) records no packet (work_packet None) rather than an invented one.; The kanban team path (_execute_via_team) packet is output_of over per-profile statuses, so result is empty and remaining_risk says no files were reported; the new test stubs plan_team to stay on the single-executor path.; Fail-before for the new tests is by absence (report() had no work_packet key); not run against the old tree.; An unfiltered run of tests/test_adversarial.py fails test_live_panel_runs_through_the_model_gateway (verdict UNAVAILABLE): a @pytest.mark.live test needing a model gateway, excluded by the gate's marker filter and not touching development.py.",
    }),
    ('BBOX-001', {
        "status": PARTIAL,
        "current_implementation": "Closed both listed gaps: reconstruct() now pages the whole event log instead of cutting at 5000, flags a truncated audit listing, and objective_trace's capability text, intent example and tool card describe the blackbox replay. (build run, commit a1c8fea92)",
        "next_action": "GAPS: The three new tests were not run against the pre-change tree; they fail there by construction (no _EVENT_PAGE/_AUDIT_LIMIT, no 'blackbox' in the card text, a 3-row cap loses the closing event).; Audit decisions are still capped at the newest 500 rows (now reported as a disagreement, not paged).; Only 22 of the 116 impact-selected test files were run, to stay within the host's memory limits; the rest were not run.",
    }),
    ('BROW-003', {
        "status": PARTIAL,
        "current_implementation": 'Browser loop now resumes after an auth/OTP/passkey hand-off (Browser.resume, pending_handoff) instead of planning on the stale page; added approved TEMPORARY profile kind with workspace ownership and guarded discard; passkey/hardware-key pages hand off. (build run, commit a41c205ac)',
        "next_action": 'GAPS: No launcher consumes ProfileChoice.directory yet: web.py still starts an in-memory Playwright context, so a TEMPORARY profile is chosen, owned and discardable but not launched on disk (needs live Chromium to verify).; Resume is pulled by the next state-changing primitive or an explicit Browser.resume(); no caller outside Browser invokes resume() or discard_temporary_profile() yet.; Did not run the new tests against the pre-change tree (git checkout is denied here); they depend on the new Browser.resume/pending_handoff and choose_profile(temporary_by_approval), so they cannot pass without the change.',
    }),
    ('CAP-004', {
        "status": PARTIAL,
        "current_implementation": 'Capability now carries read_only, consequential, reversible, idempotent, external_side_effect, requires_confirmation. All 192 are classified from side_effect/risk/policy plus two small override sets. registry_problems() refuses undeclared hints and a consequential capability with no confirmation path; it runs at import. (build run, commit 8142d753e)',
        "next_action": 'GAPS: Classification is derived (consequential = mutation above LOW risk; requires_confirmation read from policy) plus _IDEMPOTENT_MUTATIONS and _NOT_REVERSIBLE sets: my assumption, not confirmed with the owner.; tests/test_prd_ledger.py::test_the_real_ledgers_are_clean fails in this worktree only because data/prd_v7_requirements.json is untracked and absent here; unrelated to this change.; scripts/prd_v7_table.py CAP-004 row not flipped; the kernel flips the ledger from the run evidence.',
    }),
    ('CAP-FORGE-002', {
        "status": PARTIAL,
        "current_implementation": 'New friday/repo_classifier.py: bounded read-only build_dossier() plus deterministic, model-free classify() into the ten classes. A prose-dominant tree with no resolvable entry point or skill/MCP/model signal is a research corpus, so a prompt corpus is never executable. (build run, commit 12604c2a8)',
        "next_action": "GAPS: Skipped here: test_hostile_tree_file_symlinks_are_not_followed needs a symlink privilege this Windows user lacks. Directory links are proven via a junction fallback; the file-link branch shares the same reparse-point check.; Assumption: 'skill pack' in the labelled set is capability_pack (several SKILL.md); exactly one SKILL.md is skill. Revisit if the PRD means one skill.; Known limit: a documentation-heavy library with no entry point (>=70% prose/data files) classifies as research_corpus, the safe direction.; The licence table is a second copy of scripts/upstream_lock.py SIGNATURES (that script must run without friday importable); a test fails if they drift.; friday/reachability.py DECLARED gets a FUTURE entry for the module: no live caller until CAP-FORGE-003/004/014 land; without it the unreachable-symbols ratchet fails (verified).; PRD_V7_LEDGER.md row left to the kernel (moves from the build run). Class-to-integration-mode mapping and sandboxed analysis are out of scope (CAP-FORGE-003/004/014).",
    }),
    # --- PKG-001: defects found testing release candidate bd7c026, 2026-10-04
    ('PKG-001', {
        "status": PARTIAL,
        "next_action": (
            "EXE DEFECTS 2026-10-04 (FRIDAY.exe built from bd7c026; validate_exe 12/12, doctor DEGRADED). (1) The doctor "
            "reports voice llm PASS while GOOGLE_API_KEY is rejected (401) - credentials must be checked by MODEL-002's "
            "semantic probe or at least one cheap authenticated call with a timeout, reported FAIL with the provider's reason "
            "and the variable's name only; a key in the wrong format is flagged before any call (a Gemini API key starts "
            "'AIza'; the 97-char 'AQ.' value here is not one); template placeholders (wss://your-project..., sk-...xxxx) FAIL. "
            "(2) Skills on disk are not registered on first run (friday-operate unregistered) - register on-disk skills "
            "automatically at startup. (3) Onboarding 0/16 is the person's answers: report it as a setup step, not a "
            "degradation. Code signing NOT_CONFIGURED needs a certificate (external; report it, do not fake it). Tests for "
            "each; scripts/validate_exe.py stays 12/12."),
    }),
    ('BROW-003', {
        "status": PARTIAL,
        "current_implementation": 'BrowserSession now launches ProfileChoice.directory as a persistent on-disk context, deletes a TEMPORARY profile when the session closes or is abandoned, and refuses a profile switch on a running session. The loop reports the profile actually launched. (build run, commit b92e3f53e)',
        "next_action": "GAPS: Launch is verified against a faked Playwright, not a live Chromium; launch_persistent_context on a real user-data dir is unverified.; No production caller passes a ProfileChoice to browser_act/_loop_browser yet: the MCP wrapper still starts ISOLATED, and the source of the person's approval for a temporary profile is not wired.; Browser.resume() is still only pulled by the next state-changing primitive; no explicit caller exists.; tests/test_phase1b.py::test_live_browser_opens_and_closes (live, needs network) failed in one run on the page-text assertion (example.com returned Spanish text); not touched by this change and excluded from the not-live gate. The new tests were not run against the pre-change tree (git checkout denied); they call session.page(profile=...), which did not exist before.",
    }),
    ('PKG-001', {
        "status": PARTIAL,
        "current_implementation": 'Doctor now classifies each credential MISSING/PLACEHOLDER/REJECTED/VALID (bounded auth probe, value never printed); placeholders and refused keys give DEGRADED naming them. Launcher skips the voice worker with one line unless LiveKit creds are usable. (build run, commit c8d113df4)',
        "next_action": 'GAPS: Acceptance (clean Windows VM ready-state) is not demonstrated: no fresh-OS run of validate_exe_clean.py was done.; The provisioning stage (Hermes, browser runtime, coding CLI with PASS/DEGRADED/FAIL and a repair path) is not built.; ONB-001 asking for the missing keys is not wired; friday/credentials.py is the classifier it can consume.; The LiveKit probe (signed ListRooms POST) is tested against a fake transport only; no real LiveKit project was available. Google and OpenAI probes were confirmed live to return REJECTED for junk keys.; SARVAM_API_KEY has no auth probe, so it stays UNVERIFIED (set, not a template), never VALID.; Judgement calls: unset LiveKit credentials stay FAIL (existing test_launcher contract) while PLACEHOLDER/REJECTED are DEGRADED; UNVERIFIED (LiveKit unreachable at launch) does not skip the worker, so a network blip does not keep voice off until the next launch.; implementer did not claim acceptance met',
    }),
    ('BG-001', {
        "status": PARTIAL,
        "current_implementation": 'Optional work is now also deferred while the person is active (<120s since last input) or the machine is on battery; URGENT priority skips those two, never pressure. Unknown signals never defer. Real Windows/psutil probes wired into governor(). (build run, commit 8702b66ac)',
        "next_action": 'GAPS: Optional work already running is not stopped when the person returns or the machine is unplugged; only new admissions are gated (reduce_running still keys on pressure alone).; No production caller of admit(OPTIONAL) exists in friday/ yet, so autolearn/digest callers must pass through this gate when they are wired.; Assumed: two priorities (BACKGROUND default, URGENT) and a 120 s away threshold (Thresholds.user_idle_s); the PRD gives neither. Revisit if the owner wants a finer scale.; Last-input idle time is read only on Windows; elsewhere it is unknown and does not defer.',
    }),
    # --- PKG-001: one more first-run gap from release candidate bd7c026 (2026-10-04)
    ('PKG-001', {
        "status": PARTIAL,
        "next_action": (
            "EXE DEFECTS 2026-10-04 (FRIDAY.exe from bd7c026: validate_exe 12/12, validate_exe_clean PASS, doctor DEGRADED). "
            "(1) The doctor reports voice llm PASS while GOOGLE_API_KEY is rejected (401): check credentials with MODEL-002's "
            "semantic probe or one cheap authenticated call with a timeout, report FAIL with the provider's reason and the "
            "variable's name only; flag a key in the wrong format before any call (a Gemini API key starts 'AIza'; the 97-char "
            "'AQ.' value here is not one); template placeholders FAIL. (2) Register on-disk skills automatically at startup "
            "(friday-operate was unregistered). (3) Onboarding 0/16 is the person's answers: a setup step, not a degradation. "
            "(4) First run creates FRIDAY's Hermes profile when Hermes is installed and the profile is missing (the live "
            "gateway test found: Hermes profile 'friday' does not exist; create it with: hermes profile create friday "
            "--clone) and the doctor reports the profile's state. Code signing NOT_CONFIGURED needs a certificate (external). "
            "Tests for each; scripts/validate_exe.py stays 12/12."),
    }),
    # --- MODEL-012: sliced after a full cascade failed on one oversized brief (2026-10-04)
    ('MODEL-012', {
        "status": MISSING,
        "next_action": (
            "SLICED 2026-10-04 (controller; advisor design in data/build_runs/MODEL-012.advice.md). Root cause: FRIDAY's live "
            "turns never reach the model gateway - spoken and typed turns use the AgentSession LLM from "
            "providers.build_resilient_llm (Gemini or LiveKit inference only), so the gateway's Claude Code provider "
            "(friday/cli_model_provider.py) is unreachable. CURRENT SLICE 1: friday/gateway_llm.py - GatewayLLM, a "
            "livekit.agents.llm.LLM over model_gateway.gateway().infer (one assistant chunk, never tool calls, gateway "
            "failure raises APIConnectionError) - and build_resilient_llm adds it last in the FallbackAdapter when a CLI "
            "provider is configured, or returns it alone when no API key builds; tests in tests/test_gateway_llm.py "
            "including the end-to-end fake-CLI turn with cost_usd recorded. LATER SLICES: 2 - durable 401 route verdicts "
            "in the tier table and per-call / daily CLI cost caps; 3 - Codex and Antigravity providers only where a "
            "no-tools mode is verified; 4 - a live text and voice turn through FRIDAY.exe."),
    }),
    ('BG-001', {
        "status": PARTIAL,
        "current_implementation": 'Governor.reduce_running now also stops running BACKGROUND optional work when the person returns or the machine goes on battery (URGENT keeps running; pressure still stops it). Lease records its priority; a raising context probe reads as unknown. (build run, commit da84d62f5)',
        "next_action": 'GAPS: No production caller of admit(OPTIONAL) exists in friday/ yet, so autolearn/digest callers must pass through this gate when they are wired.; Assumed: two priorities (BACKGROUND default, URGENT) and a 120 s away threshold (Thresholds.user_idle_s); the PRD gives neither. Revisit if the owner wants a finer scale.; Last-input idle time is read only on Windows; elsewhere it is unknown and does not defer.; Running work is re-judged only on a fresh pressure sample (sample_ttl_s, 2 s) and only when something calls pressure(); there is no background ticker.',
    }),
    ('PKG-001', {
        "status": PARTIAL,
        "current_implementation": "Voice row FAILs on a template/refused/wrong-format key (Gemini 'AIza' checked pre-call; provider reason code, name only). Startup registers on-disk skills and creates the friday Hermes profile; doctor reports the profile; onboarding is a setup step (PASS). (build run, commit 9bd8a1324)",
        "next_action": 'GAPS: Acceptance is a clean Windows VM ready-state: not run here (no VM, no rebuilt FRIDAY.exe); scripts/validate_exe.py 12/12 not re-run against a rebuilt exe; Skills are registered at launcher start (run_all) only, not when server.py runs outside FRIDAY.exe; Provider credentials row stays DEGRADED for a refused key; only the voice row (the stage that cannot run) is FAIL; New tests were not run against the pre-change tree (git checkout is denied); they call new functions or assert FAIL/REJECTED where the old code gave PASS/UNVERIFIED; implementer did not claim acceptance met',
    }),
    ('MODEL-012', {
        "status": PARTIAL,
        "current_implementation": 'Slice 1: GatewayLLM (LiveKit LLM over gateway().infer, one chunk, no tool calls, APIConnectionError on failure); build_resilient_llm appends it last or returns it alone with no API key. 15 new tests incl. fake-CLI turn with cost recorded. (build run, commit 89f144ef6)',
        "next_action": "GAPS: 3 failures in tests/test_voice_path.py (tts_produces_real_audio, tts_stt_round_trip, stt_needs_an_http_session_outside_a_job): real OpenAI TTS calls get 401 invalid_api_key on this host; they use build_tts/build_stt, untouched by this slice. Not run against the pre-change tree.; Slice 2: durable 401 route verdicts in the tier table (a rejected key is still retried every ~2 min) and per-call / daily CLI cost caps (owner cap $40 soft / $45 hard is not enforced on this route).; Slice 3: Codex and Antigravity backends - only where a no-tools mode is verified (Codex 0.121.0 here is too old; Antigravity has no headless completion command).; Slice 4: a live text and voice turn through FRIDAY.exe; voice also needs working LiveKit and TTS keys (.env holds placeholders). Nothing here was run against the real claude CLI or the exe.; build_resilient_llm treats 'CLI configured' as discover_backends() non-empty (no configured() exists): enabled, not installed or signed in. With no API key and no usable CLI the failure surfaces at turn time as APIConnectionError, not at startup.; With the CLI route the voice agent has no tools: GatewayLLM ignores them, so a fallback turn cannot act, only reply.; A cancelled stream (barge-in) cannot stop the in-flight CLI call in its thread; the call completes, is paid and recorded, and its text is discarded. FallbackAdapter recovery probes also issue a real CLI call after a GatewayLLM failure (one at a time).; implementer did not claim acceptance met",
    }),
    ('CODE-001', {
        "status": PARTIAL,
        "current_implementation": 'Added scripts/minimal_build_ab.py, an A/B harness for the PRD s23 ladder: 5-task corpus with held-out acceptance and guard probes, diff/cost/quality measurement, verdict, report. Fake runner only; no live run yet, so the row stays PARTIAL. (build run, commit f494c8206)',
        "next_action": "GAPS: No live run recorded: the report in docs/implementation/MINIMAL_BUILD_AB.md is SYNTHETIC (fake runner, evidence false, verdict forced INCONCLUSIVE). A real A/B report needs `scripts/minimal_build_ab.py --live --budget-usd <X>` under the day spend ledger; the live path (ClaudeRunner -> build_kernel.worker_command) is unit-tested with a stub only, never run against the real claude CLI.; Ledger row not edited: CODE-001 stays MISSING in scripts/prd_v7_table.py and PRD_V7_LEDGER.md; moving it to PARTIAL goes through PROGRESS_V7 in scripts/prd_ledger.py (the kernel appends it from this evidence). The tests/test_prd_ledger.py matrix count is unchanged because no row was added.; The 5 corpus tasks are new standalone modules (friday/durations.py etc.) pinned to base 35bd572, not edits to existing FRIDAY code, so the ladder's 'already in the codebase' rung is barely exercised; a live run can only speak for tasks of this shape.; The new tests were not run against the pre-change tree: they import scripts/minimal_build_ab.py, which did not exist, so they fail by construction (ImportError).; MIN_PAIRS=3 and MIN_RUN_USD=0.25 are assumed thresholds; the PRD gives neither. Revisit if the owner wants other values.; JetBrains' ~10% figure is recorded only as a hypothesis, taken from the build packet; no source was checked.; implementer did not claim acceptance met",
    }),
    ('CHAOS-001', {
        "status": PARTIAL,
        "current_implementation": 'Chaos suite maps all 9 PRD faults to tests (FAULT_MAP) and writes a per-fault report (9/9 green). Fault injection found two real defects, now fixed: dropped links were non-retryable, and a non-envelope tool result was stored as success. (build run, commit 670c60e89)',
        "next_action": "GAPS: Ledger row PRD_V7_LEDGER.md CHAOS-001 not edited: the kernel records the status from the run (earlier rows are 'docs(ledger): ... from build run').; The packet names .venv-verify; it does not exist on this machine, so the report was generated with .venv-build (interpreter path is printed in the report).; hermes_crash, invalid_model_response, provider_outage and db_contention reuse earlier tests rather than new injections; the invalid-model-response tests (tests/test_resilience.py) check that an empty completion is recognised, not a durable end state on a task.; Browser crash is injected as a Playwright-style TargetClosedError raised by the capability port; no real browser process is killed, and friday/browser*.py was not read.; Network loss is simulated by raising ConnectionError/socket.gaierror and a NOT_CONFIGURED envelope from the port; no real link is dropped.; Reboot is simulated by dropping the Store and opening the file with a new executor id; a real OS reboot / Modern Standby run was not performed.",
    }),
    ('CHAOS-001', {
        "status": PARTIAL,
        "current_implementation": 'Chaos report regenerated 9/9 green. New invalid-model-answer injection at the planner found a real defect: wrong-shape JSON or empty text crashed or was memoised. Planner now falls back to the deterministic plan and caches nothing. (build run, commit 8d420c294)',
        "next_action": 'GAPS: PRD_V7_LEDGER.md CHAOS-001 row not edited: the kernel records the status from the run.; .venv-verify does not exist on this machine; the report was generated with .venv-build (interpreter path is printed in the report).; hermes_crash, provider_outage and db_contention still reuse earlier tests rather than new injections.; Browser crash is a Playwright-style TargetClosedError raised by the capability port; no real browser process is killed and friday/browser*.py was not read.; Network loss is simulated by raising ConnectionError/socket.gaierror and a NOT_CONFIGURED envelope from the port; no real link is dropped.; Reboot is simulated by dropping the Store and opening the file with a new executor id; a real OS reboot / Modern Standby run was not performed.; Invalid model answer is injected at planner_model.interpret with a scripted genai client; the other model doors (ModelGateway.infer) were not injected.',
    }),
    ('SEC-005', {
        "status": PARTIAL,
        "next_action": (
            "RULING 2026-10-05b (owner, docs/plans/build-harness/00-owner-directive-2026-10-05b.md): keep Hermes'"
            " own tools - 'i want hermes files and other thing to be used by friday too and hermes too'. The frid"
            'ay profile keeps its native file/terminal toolsets ON, and a scoped delegation is no longer refused '
            'for that (friday/hermes_bridge.py delegate: the OutsideEnvelope raised when native_file_toolsets_ena'
            'bled() is non-empty). STATICALLY_CONFIRMED (installed Hermes, hermes-agent/tui_gateway/server.py): _'
            'load_enabled_toolsets() (line 933) reads HERMES_TUI_TOOLSETS, a comma list, and _make_agent passes e'
            "nabled_toolsets=_load_enabled_toolsets() (line 2039), so the variable replaces the profile's toolset"
            's for that gateway process; session.create takes no toolset parameter. DESIGN (decided): an unscoped'
            ' delegation runs on the shared gateway exactly as now; a scoped delegation runs on a second gateway '
            "process started with HERMES_TUI_TOOLSETS = the profile's enabled toolsets minus file and terminal, w"
            "ith FRIDAY's exec bridge registered as now - so its only file access is the jail-backed capability a"
            'nd commands stay DISABLED (ruling 2026-10-03c). Start it on the first scoped delegation and stop it '
            'when none holds it. If the profile names no toolsets, refuse the scoped delegation with that reason '
            "rather than guess Hermes' defaults. Tests (Windows, the fake gateway of tests/test_delegation_scope."
            'py): with native tools on in the profile config a scoped delegation is accepted and its gateway env '
            'carries HERMES_TUI_TOOLSETS without file/terminal; an unscoped one uses the shared gateway with no o'
            'verride; an override that would still contain file or terminal refuses the delegation; the existing '
            "scope tests stay green. Do not edit Hermes' internals."
        ),
    }),
    ('MODEL-012', {
        "status": PARTIAL,
        "next_action": (
            "RULING 2026-10-05b (owner, docs/plans/build-harness/00-owner-directive-2026-10-05b.md): 'use opencod"
            "e free models and codex and claude code login on ur own if they are signed in just use them'. SLICE "
            '3 now: an OpenCodeBackend in friday/cli_model_provider.py (BACKENDS; DEFAULT_ENABLED gains it after '
            "claude) for opencode's free models. STATICALLY_CONFIRMED on this host 2026-10-05: opencode 1.18.32 o"
            'n PATH; `opencode models` lists free models (opencode/big-pickle, opencode/nemotron-3.5-lightning-fr'
            'ee, opencode/mimo-v2.6-flash-free, ...); `opencode run -m opencode/big-pickle --format json "<prompt'
            '>"` with stdin CLOSED answered PONG, exit 0, JSON events step_start / text (part.text) / step_finish'
            ' (part.cost=0, part.tokens.input=58898); with stdin left open it hung (120 s timeout, no output). Th'
            "e 58,898 input tokens are opencode's default agent prompt and tool definitions, so run it with a FRI"
            'DAY-owned no-tools agent (an opencode config in the scratch cwd with every tool disabled, selected w'
            'ith --agent or OPENCODE_CONFIG) and prove the input tokens drop and no tool event appears. route_kin'
            'd cli, cost 0 recorded, cwd a scratch dir. Codex: the controller upgraded codex-cli 0.121.0 -> 0.160'
            ".0, still 'Logged in using ChatGPT'; `codex exec` has `-s read-only` and `--skip-git-repo-check`. Bu"
            'ild a CodexBackend only if a no-tools or read-only answer-only mode is verified with a fake and one '
            'live call; otherwise record why not. Claude Code stays the first CLI route. Tests: fake opencode and'
            " codex binaries in the style of tests/test_cli_model_provider.py's fake claude (parse, no-tools conf"
            'ig, closed stdin, timeout, sign-in check); one live test each, marked live.'
        ),
    }),
    ('COG-002', {
        "status": PARTIAL,
        "current_implementation": 'Golden eval (33 labelled tasks): adaptive effort is 35.2% cheaper than always-deep at 0.94 first-pass quality (threshold 0.90, deep floor intact). It found and fixed two routing defects; results go through RouteOutcomes and a frozen baseline. (build run, commit 762a435e8)',
        "next_action": "GAPS: QUALITY_THRESHOLD=0.90 and the 1:3:5 price ladder are assumptions; the PRD names a threshold but never sets it; Quality oracle is human labels (min_tier), not measured model output; costs are modelled price-weighted tokens, not live usage; Two labelled misses remain (reconnect, stale-cache): classify_task treats requests with no listed work verb ('make', 'stop') as questions and routes them to economy; A mechanical phrase still beats a code-change verb (e.g. 'fix the word count display' routes deterministic); not measured by the suite, not fixed",
    }),
    ('COG-005', {
        "status": PARTIAL,
        "current_implementation": 'Added friday/simple_loop.py: strict ActionSpec, bounded act/observe/update loop via build_dispatch and the envelope, escalation to objective_start. Not yet called live. (build run, commit 8c5626183)',
        "next_action": 'GAPS: No live caller: the voice turn does not yet route a SIMPLE class to run_simple; declared FUTURE in friday/reachability.py DECLARED.; The $0.10 bound is metered from the proposer-reported Proposal.cost_usd, not the kernel spend lease, which was not located; owner to confirm.; Escalation hands the original request to objective_start after any actions already ran; Step records are returned but not passed to the planner, so a side-effecting step could be planned again.; objectives.py and planner.py unchanged: the existing objectives.start entry (which calls the planner and compile_objective) is the escalation seam, so the planned objectives.py hook was not needed.; Ran in .venv-build, not .venv-verify (absent on this machine). Regression: test_task_class.py and test_objective_budget.py passed (76 passed with test_reachability and test_simple_loop).',
    }),
    ('COG-002', {
        "status": PARTIAL,
        "current_implementation": "Fixed the two routing misses: imperative 'make X'/'stop X from Y' are now code changes, not questions; a work verb beats a mechanical phrase. Golden eval: quality 0.94 -> 1.00, 38.8% cheaper than always-deep (was 35.2%), deep floor intact. Baseline re-frozen. (build run, commit 9ffa3d03b)",
        "next_action": "GAPS: QUALITY_THRESHOLD=0.90 and the 1:3:5 price ladder are still assumptions; the PRD names a threshold but never sets it; Quality oracle is still human min_tier labels, not measured model output; costs are modelled price-weighted tokens, not live usage; Two over-provisioned tasks remain in the golden suite (default-timeout routed standard, labelled economy; compare-stores); Work-verb list is still a fixed set (change, fix, implement, refactor, build, write, create, add, make, prevent, stop...from); other imperatives such as update/remove/replace with no listed verb still fall through to question; The new golden-suite rows are the two previously-labelled misses; the suite does not yet cover mechanical-vs-work-verb phrasing like 'fix the word count display' (covered by unit tests only)",
    }),
]


def apply_progress(rows: list[dict],
                   progress: list[tuple[str, dict]] | None = None) -> list[dict]:
    """Fold a PROGRESS list (PROGRESS by default) over the audited rows."""
    by_id = {row["id"]: dict(row) for row in rows}
    for rid, changes in (PROGRESS if progress is None else progress):
        if rid not in by_id:
            raise KeyError(f"PROGRESS names {rid!r}, which is not a requirement")
        unknown = set(changes) - set(by_id[rid])
        if unknown:
            raise KeyError(f"PROGRESS for {rid} sets unknown field(s) {sorted(unknown)}")
        by_id[rid].update(changes)
        if changes.get("status") in TERMINAL:
            by_id[rid]["next_action"] = changes.get("next_action", "")
    return [by_id[row["id"]] for row in rows]


# --- rendering ---------------------------------------------------------------

def _head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, timeout=30
                              ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _dirty() -> int:
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                             capture_output=True, text=True, timeout=60).stdout
        return len([line for line in out.splitlines() if line.strip()])
    except Exception:
        return -1


def prd_identity(path: Path) -> dict:
    if not path.exists():
        return {"file": str(path), "present": False}
    raw = path.read_bytes()
    return {"file": str(path), "present": True,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "lines": raw.count(b"\n") + 1}


def counts(rows: list[dict]) -> dict:
    out = {state: 0 for state in sorted(ALL_STATES)}
    for row in rows:
        out[row["status"]] = out.get(row["status"], 0) + 1
    return {k: v for k, v in out.items() if v}


def unresolved(rows: list[dict], priority: str) -> list[str]:
    return [r["id"] for r in rows
            if r["priority"] == priority and r["status"] in WORKING]


_CITED_FILE = re.compile(r"^(?P<path>[\w./\\-]+?)(?::(?P<lines>[\d,\s-]*\d[\d,\s-]*))?$")


def _inside(root: Path, rel: str) -> Path | None:
    """The cited path, or None when it would leave the tree it maps."""
    path = (root / rel).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    return path


def _collectable(path: Path) -> tuple[set[str], dict[str, set[str]]]:
    """Test functions and Test-class methods pytest would collect from a file
    under its default naming (the repo sets no override). Parsed, not
    grepped, so a helper or a `def` quoted in a docstring does not count."""
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    funcs = {n.name for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name.startswith("test")}
    classes = {n.name: {m.name for m in n.body
                        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and m.name.startswith("test")}
               for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name.startswith("Test")}
    return funcs, classes


def _citation_problems(row: dict, root: Path) -> list[str]:
    """A v7 row maps the tree (v7 CORE-002), so every citation must resolve
    on it: a `files` entry is `path` or `path:line[,line|-line]` naming a
    file inside the tree and lines that exist in it, and a `tests` entry
    names a test file and, after `::`, a test pytest would collect from it.

    Line numbers are range-checked only; they drift as code moves and are
    not anchors. A VERIFIED row therefore rests on a named test, which is
    resolved exactly."""
    rid, problems = row["id"], []
    for entry in row["files"]:
        m = _CITED_FILE.match(entry.strip())
        if not m:
            problems.append(f"{rid}: files entry {entry!r} is not a path")
            continue
        path = _inside(root, m.group("path"))
        if path is None:
            problems.append(f"{rid}: cited path {m.group('path')} leaves the tree")
            continue
        if not m.group("lines"):
            if not path.exists():   # a bare path may name a directory
                problems.append(f"{rid}: cited path {m.group('path')} does not exist")
            continue
        if not path.is_file():
            problems.append(f"{rid}: cited file {m.group('path')} does not exist")
            continue
        numbers = [int(n) for n in re.findall(r"\d+", m.group("lines"))]
        length = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        if min(numbers) < 1 or max(numbers) > length:
            problems.append(f"{rid}: {entry} cites a line outside a {length}-line file")
    for entry in row["tests"]:
        file_part, _, node = entry.strip().partition("::")
        path = _inside(root, file_part)
        if path is None or not path.is_file():
            problems.append(f"{rid}: cited test file {file_part} does not exist in the tree")
            continue
        if not node:
            continue
        parts = [p.split("[")[0] for p in node.split("::")]
        funcs, classes = _collectable(path)
        found = ((len(parts) == 1 and parts[0] in funcs)
                 or (len(parts) == 2 and parts[1] in classes.get(parts[0], ())))
        if not found:
            problems.append(f"{rid}: cited test {entry} is not a collectable test")
    return problems


def check(rows: list[dict], crosswalk: set[str] | None = None,
          root: Path = ROOT) -> list[str]:
    """The rule that keeps the ledger honest. `crosswalk` is the set of v1
    ids a v7 row's `maps_to_v1` may name; `root` is the tree v7 citations
    must resolve against."""
    problems = []
    seen = set()
    ids = {r["id"] for r in rows}
    for row in rows:
        rid = row["id"]
        if rid in seen:
            problems.append(f"{rid}: duplicate id")
        seen.add(rid)
        if row["status"] not in ALL_STATES:
            problems.append(f"{rid}: unknown status {row['status']!r}")
        if row["status"] == VERIFIED and not row["evidence"]:
            problems.append(f"{rid}: VERIFIED without evidence")
        if ("acceptance" in row and row["status"] == VERIFIED
                and not any("::" in t for t in row["tests"])):
            # v7 section 0.4: code existing is not evidence. A v7 row is only
            # VERIFIED by a test that ran, so it names one - a test, not just
            # a file that might hold one.
            problems.append(f"{rid}: VERIFIED v7 row names no test")
        if row["status"] == BLOCKED_EXTERNAL and not row["blocker"]:
            problems.append(f"{rid}: BLOCKED_EXTERNAL without a blocker")
        if row["status"] == REJECTED_WITH_EVIDENCE and not row["evidence"]:
            problems.append(f"{rid}: REJECTED_WITH_EVIDENCE without evidence")
        if row["status"] in WORKING and not row["next_action"]:
            problems.append(f"{rid}: {row['status']} without a next action")
        for dep in row["dependencies"]:
            if dep not in ids:
                problems.append(f"{rid}: dependency {dep} is not a requirement")
        for v1 in row.get("maps_to_v1", ()):
            if crosswalk is None or v1 not in crosswalk:
                problems.append(f"{rid}: maps_to_v1 names {v1}, which is not a v1 requirement")
        if "acceptance" in row:
            problems += _citation_problems(row, root)
    return problems


def render_markdown(rows: list[dict], meta: dict,
                    title: str = "PRD Requirements Ledger") -> str:
    by_section: dict[str, list[dict]] = {}
    for row in rows:
        by_section.setdefault(row["section"], []).append(row)
    v7 = any("acceptance" in row for row in rows)

    lines = [
        f"# {title}",
        "",
        "Generated by `scripts/prd_ledger.py` - edit the table in that file, not this",
        "document. Every row's status is the audited state, and the generator writes",
        "no ledger at all while its table has a problem: a VERIFIED row with no",
        "evidence, a BLOCKED_EXTERNAL row with no named blocker, a working row with",
        "no next action, or (v7) a VERIFIED row naming no test or a citation that",
        "does not resolve on the tree.",
        "",
        f"- PRD: `{meta['prd']['file']}`",
        f"- PRD sha256: `{meta['prd'].get('sha256', 'absent')}`",
        f"- PRD size: {meta['prd'].get('lines', 0)} lines, {meta['prd'].get('bytes', 0)} bytes",
        f"- Repository HEAD: `{meta['head']}`",
        f"- Uncommitted paths at generation: {meta['dirty_paths']}",
        f"- Generated: {meta['generated']}",
        "",
        "## Status vocabulary",
        "",
        "Terminal (the program may end on these): `VERIFIED`, `BLOCKED_EXTERNAL`,",
        "`DEFERRED_BY_PRD`, `REJECTED_WITH_EVIDENCE`, `NOT_APPLICABLE`.",
        "",
        "Working (the program may not end on these): `PARTIAL`, `EXISTING_UNVERIFIED`,",
        "`MISSING`, `IMPLEMENTING`, `TESTING`, `NOT_AUDITED`, `BROKEN`, `DUPLICATED`,",
        "`SUPERSEDED`.",
        "",
        "## Roll-up",
        "",
        "| state | count |",
        "|---|---:|",
    ]
    for state, n in sorted(counts(rows).items(), key=lambda kv: -kv[1]):
        lines.append(f"| {state} | {n} |")
    lines += [
        f"| **total** | **{len(rows)}** |",
        "",
        f"Unresolved P0: {len(unresolved(rows, P0))} "
        f"({', '.join(unresolved(rows, P0)) or 'none'})",
        "",
        f"Unresolved P1: {len(unresolved(rows, P1))} "
        f"({', '.join(unresolved(rows, P1)) or 'none'})",
        "",
    ]

    for section in sorted(by_section, key=_section_key):
        if v7:
            lines += [f"## PRD section {section}", "",
                      "| id | P | status | requirement | acceptance evidence | v1 rows | where | evidence / blocker / next |",
                      "|---|---|---|---|---|---|---|---|"]
        else:
            lines += [f"## PRD section {section}", "",
                      "| id | P | status | requirement | where | evidence / blocker / next |",
                      "|---|---|---|---|---|---|"]
        for row in by_section[section]:
            where = ", ".join(f"`{f}`" for f in row["files"][:3]) or "-"
            note = row["evidence"] or row["blocker"] or row["next_action"] or "-"
            summary = row["summary"]
            if v7:
                lines.append(
                    f"| {row['id']} | {row['priority']} | **{row['status']}** | "
                    f"{_cell(summary)} | {_cell(row['acceptance'])} | "
                    f"{', '.join(row['maps_to_v1']) or '-'} | {where} | {_cell(note)} |")
            else:
                lines.append(
                    f"| {row['id']} | {row['priority']} | **{row['status']}** | "
                    f"{_cell(summary)} | {where} | {_cell(note)} |")
        lines.append("")

    owner = [r for r in rows if r["owner_direction"]]
    if owner:
        lines += ["## Owner direction recorded against a requirement", ""]
        for row in owner:
            lines.append(f"- **{row['id']}** - {_cell(row['owner_direction'])}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _section_key(section: str):
    try:
        return (0, float(section))
    except ValueError:
        return (1, section)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def verdict(rows: list[dict], meta: dict) -> dict:
    c = counts(rows)
    working = sum(v for k, v in c.items() if k in WORKING)
    if working == 0:
        status = "PASS" if c.get(VERIFIED, 0) else "BLOCKED"
    elif c.get(VERIFIED, 0) + c.get(DEFERRED_BY_PRD, 0) > 0:
        status = "PARTIAL"
    else:
        status = "BLOCKED"
    return {
        "prd_status": status,
        "head": meta["head"],
        "prd_sha256": meta["prd"].get("sha256"),
        "generated": meta["generated"],
        "requirements": {
            "total": len(rows),
            "verified": c.get(VERIFIED, 0),
            "blocked_external": c.get(BLOCKED_EXTERNAL, 0),
            "deferred": c.get(DEFERRED_BY_PRD, 0),
            "rejected": c.get(REJECTED_WITH_EVIDENCE, 0),
            "not_applicable": c.get(NOT_APPLICABLE, 0),
            "working": working,
            "by_state": c,
        },
        "unresolved_p0": len(unresolved(rows, P0)),
        "unresolved_p0_ids": unresolved(rows, P0),
        "unresolved_p1": len(unresolved(rows, P1)),
        "unresolved_p1_ids": unresolved(rows, P1),
    }


# set -> (ledger json, verdict json, markdown ledger, markdown title)
ARTIFACTS = {
    "v1": ("prd_requirements.json", "prd_final_verdict.json",
           "PRD_REQUIREMENTS_LEDGER.md", "PRD Requirements Ledger"),
    "v7": ("prd_v7_requirements.json", "prd_v7_verdict.json",
           "PRD_V7_LEDGER.md", "PRD v7 Requirements Ledger"),
}


def write_set(name: str, rows: list[dict], prd: Path, json_only: bool) -> None:
    ledger_json, verdict_json, ledger_md, title = ARTIFACTS[name]
    meta = {
        "prd": prd_identity(prd),
        "head": _head(),
        "dirty_paths": _dirty(),
        "generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "docs" / "implementation").mkdir(parents=True, exist_ok=True)

    payload = {"meta": meta, "requirements": rows}
    (ROOT / "data" / ledger_json).write_text(
        json.dumps(payload, indent=1), encoding="utf-8")
    (ROOT / "data" / verdict_json).write_text(
        json.dumps(verdict(rows, meta), indent=1), encoding="utf-8")
    if not json_only:
        (ROOT / "docs" / "implementation" / ledger_md).write_text(
            render_markdown(rows, meta, title), encoding="utf-8")

    v = verdict(rows, meta)
    print(f"{name}: {len(rows)} requirements -> {v['prd_status']}; "
          f"verified {v['requirements']['verified']}, "
          f"working {v['requirements']['working']}, "
          f"unresolved P0 {v['unresolved_p0']}, P1 {v['unresolved_p1']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prd", default=str(Path.home() / "Downloads" / "PRD.md"),
                    help="the PRD v1.0 file the v1 set was extracted from")
    ap.add_argument("--prd-v7", default=str(ROOT / "docs" / "PRD_V7.md"),
                    help="the PRD v7.0 file the v7 set was extracted from")
    ap.add_argument("--set", choices=("v1", "v7", "all"), default="all",
                    help="which requirement set to validate and regenerate")
    ap.add_argument("--check", action="store_true",
                    help="validate the table and exit non-zero on a problem")
    ap.add_argument("--json-only", action="store_true")
    args = ap.parse_args(argv)

    v1_rows = apply_progress(REQUIREMENTS)
    sets: dict[str, tuple[list[dict], Path, set[str] | None]] = {}
    if args.set in ("v1", "all"):
        sets["v1"] = (v1_rows, Path(args.prd), None)
    if args.set in ("v7", "all"):
        sets["v7"] = (apply_progress(_load_v7(), PROGRESS_V7), Path(args.prd_v7),
                      {row["id"] for row in v1_rows})

    status = 0
    for name, (rows, prd, crosswalk) in sets.items():
        problems = check(rows, crosswalk)
        for p in problems:
            print(f"LEDGER PROBLEM [{name}]: {p}", file=sys.stderr)
        if problems:
            # A table with a problem does not get a ledger: the previous
            # artifacts stay as they were rather than being replaced by one
            # that reads cleaner than the table behind it.
            status = 1
            print(f"{name}: {len(problems)} problem(s); artifacts not written",
                  file=sys.stderr)
            continue
        if args.check:
            print(f"ledger {name} OK: {len(rows)} requirements, no problems")
            continue
        write_set(name, rows, prd, args.json_only)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
