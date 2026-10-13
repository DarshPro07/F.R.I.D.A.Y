# Owner directive 2026-10-13b — Harvey + Claude Ads as business capabilities

Source: the owner's message in the build-controller session on 2026-10-13, sent right after the
rulings in `00-owner-directive-2026-10-13.md`. Recorded verbatim below; only web-citation rendering
artefacts (favicon image links, "GitHub +1"-style source chips, "&#x20;" entities) were removed.
This is new scope outside PRD v7: research and adoption-matrix first, amendment rows only for the
components the matrix approves. It must not interrupt P0 execution and sandbox work.

---

# Adding Harvey + Claude Ads to FRIDAY

I researched both repositories. I recommend adopting useful parts of both, but not installing either as a second autonomous controller inside FRIDAY.

These are business automation capabilities, not solutions to Claude Code's quota exhaustion or `/model` switching. They can make FRIDAY much stronger at sales outreach and paid advertising, while your existing Build Kernel continues handling model failover and development.

## Research findings

1\. [Harvey — Autonomous AI Sales Agent](https://github.com/ethanplusai/harvey)

Adapt selectively

Harvey is designed to discover prospects, qualify leads, write personalized cold emails, handle replies and manage outreach campaigns.

Its published architecture includes prospecting, writing, sending, reply-handling and analysis agents, with SQLite state and a Claude Code headless execution loop. The repository is MIT-licensed.

2\. [Claude Ads — Paid Media Operations](https://github.com/AgriciDaniel/claude-ads)

Higher-priority pilot

Claude Ads is a structured advertising operations toolkit supporting Meta, Google, YouTube, LinkedIn, TikTok and seven other advertising platforms.

It provides evidence-backed audits, campaign plans, creative workflows, monitoring, deterministic scoring and versioned reports. It is read-only by default, with explicit approval controls for account changes. Its original code is MIT-licensed.

## Which features belong inside FRIDAY?

| Capability                     | Harvey         | Claude Ads           | FRIDAY integration                  |
| ------------------------------ | -------------- | -------------------- | ----------------------------------- |
| Prospect discovery             | Strong fit     | Limited              | Sales capability pack               |
| Lead qualification             | Strong fit     | —                    | Reuse FRIDAY skills                 |
| Personalized email sequences   | Strong fit     | —                    | Draft-first outreach                |
| Reply handling                 | Strong fit     | —                    | CRM + approval workflow             |
| Meta Ads audits                | —              | Strong fit           | Paid-media capability pack          |
| Campaign performance analysis  | —              | Strong fit           | Analytics and recommendations       |
| Creative/copy generation       | Sales-oriented | Advertising-oriented | Reusable content skills             |
| Autonomous external actions    | Yes            | Capability-gated     | FRIDAY policy remains authoritative |
| Claude/Codex/OpenCode failover | Not provided   | Not provided         | Existing Build Kernel               |

My priority order: integrate a read-only Claude Ads pilot first, then extract Harvey's sales workflows. Neither should interrupt the current P0 execution and sandbox work.

## Two changes I would make before integrating them

Harvey should not run its default Claude-based heartbeat continuously. Its README describes a recurring loop using `claude -p` and tracks Claude subscription usage. That could consume the exact Claude allowance you're trying to preserve for FRIDAY development. Replace unnecessary model-driven polling with event triggers, deterministic checks and your existing Codex/OpenCode routing.

Disable Harvey's LinkedIn scraping integration. LinkedIn explicitly prohibits unauthorized scraping and browser automation. Prefer permissioned APIs, lawful lead sources, company-owned data and approved contact lists.

For Claude Ads, the most valuable components are its typed findings, evidence scoring and account-mutation safety gates. Don't automatically load all 12 platform skills into every Claude Code session. Load only the platform required for the task.

## Implementation prompt for your existing Claude Code harness

# FRIDAY — Integrate Harvey and Claude Ads as Cost-Efficient Business Capabilities

## Objective

Research, evaluate and selectively integrate these repositories into the existing FRIDAY ecosystem:

[https://github.com/ethanplusai/harvey](https://github.com/ethanplusai/harvey)

[https://github.com/AgriciDaniel/claude-ads](https://github.com/AgriciDaniel/claude-ads)

FRIDAY remains the universal supervisor. These repositories should extend its capabilities in B2B sales, lead generation, outreach and paid advertising.

Do not replace FRIDAY's existing scheduler, model gateway, capability registry, skill lifecycle, memory, resource governor or policy authority.

## 1. Audit before installing

Inspect the current FRIDAY repository, authoritative PRD, requirement ledger and implementation state.

Clone both repositories at verified immutable commit SHAs into the approved quarantine area.

Inspect their actual code, licenses, dependencies, installation scripts, permissions, security posture and tests.

Run the mandatory real SkillSpector scan and sandbox qualification.

Compare every proposed capability against FRIDAY's existing sales, outreach, research, marketing and workflow capabilities.

Classify each component as USE, WRAP, EXTEND, EXTRACT_PATTERN, REFERENCE_ONLY or REJECT.

Reject redundant or unsafe integrations. Preserve licensing and provenance.

## 2. Harvey — Sales Intelligence Capability Pack

Evaluate reusable components for prospect discovery, company research, ICP matching, lead scoring, personalized outreach, email sequencing, reply categorization, objection handling, follow-up planning, meeting qualification and sales analytics.

Use the existing FRIDAY objective engine and event runtime.

Do not deploy Harvey's independent heartbeat loop, permanent Claude-only brain or competing SQLite authority into FRIDAY.

Adapt useful components as bounded capabilities and reusable skills.

Integrate with existing CRM capabilities where available. Keep external services optional.

Block unauthorized LinkedIn scraping, unapproved email sending and automatic outreach based on unverified contact data.

Enforce suppression lists, opt-outs, sender identity, deliverability controls, privacy requirements and explicit sending approval.

Start with prospect research, lead qualification and draft outreach only.

## 3. Claude Ads — Paid Media Capability Pack

Evaluate its Meta Ads, Google Ads and other platform modules, typed schemas, audit workers, deterministic scoring, report rendering, monitoring and campaign-planning workflows.

Start with Meta Ads as the first pilot.

Support campaign audit, spend and ROAS analysis, creative fatigue detection, funnel analysis, tracking checks, creative briefs, budget recommendations and experiment planning.

Keep all operations read-only or draft-only initially.

No campaign publishing, budget adjustment, pause/resume, audience change or asset publishing without specific account-level authorization, before/after diff, idempotency, rollback and post-state verification.

Reuse existing FRIDAY reporting, evidence and policy infrastructure rather than importing duplicate control planes.

## 4. Multi-model and token efficiency

Do not hardcode Claude as the required reasoning provider.

Route through the existing qualified execution adapters:

- Deterministic code for polling, scoring, data transformation, scheduling and numerical analysis.
- Qualified OpenCode free models for permitted low-risk extraction and drafting.
- Codex or another benchmarked worker for integration development.
- Claude Sonnet only when measured quality justifies it.
- Opus only for unusually difficult or consequential reasoning.

Maintain automatic quota exhaustion handling and durable continuation.

Never fall back to a paid API without explicit authorization.

Load skills only on demand. Avoid full repository or marketing-playbook dumps in every model prompt.

Measure provider usage and cost per verified completed task.

## 5. First two pilot journeys

**Sales pilot — AiCodePro**

Research an authorized sample of B2B companies, identify relevant website or AI-visibility opportunities, produce a scored lead report, generate personalized draft outreach and stage follow-ups for human review.

Verify research sources, qualification evidence and CRM persistence.

Do not send messages during the initial pilot.

**Advertising pilot — Crunchy Fashion**

Use authorized sample Meta Ads exports to perform a read-only campaign audit.

Produce campaign performance findings, creative recommendations, tracking issues, a prioritized optimization plan and proposed experiments.

Calculate metrics deterministically and distinguish observed data from estimates.

Do not change the live advertising account.

## 6. Tests and promotion

Use frozen representative fixtures and negative controls.

Prove workspace isolation, approved data access, no unauthorized network calls, no duplicate sends, correct opt-out handling, no unapproved ad mutations, safe restart and recovery, and faithful evidence reporting.

Benchmark each integration against existing FRIDAY capabilities before activation.

Do not promote a skill merely because it installs or passes its own tests. Require a real representative journey with independent verification.

Keep original FRIDAY implementation progress uninterrupted.

## Required output

Provide a component-by-component adoption matrix, exact source commits, license assessment, duplicate-capability findings, scanner results, architectural changes, tests, pilot outcomes, model/token costs and unresolved blockers.

Implement only approved, non-duplicative components. Leave all other discoveries in the Opportunity Library.

**Success means FRIDAY gains independently verified sales and advertising capabilities without another orchestration framework, unexpected Claude quota consumption, unauthorized external actions or higher cost for equivalent work.**

### Optional: Claude Ads directly inside Claude Code

After auditing the repository, its documented native installation is:

```
/plugin marketplace add AgriciDaniel/claude-ads
/plugin install claude-ads@ai-marketing-hub-claude-ads
```

The plugin command is `/claude-ads:ads`, and the project also supports a Windows PowerShell installation workflow.

My final decision: Claude Ads is worth testing as a direct Claude Code skill and a future FRIDAY capability adapter. Harvey is better treated as a source of reusable sales capabilities, not another always-running agent.

That keeps your system efficient while expanding what FRIDAY can actually do.
