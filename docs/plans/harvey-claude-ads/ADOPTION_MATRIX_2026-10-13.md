# Harvey + Claude Ads — adoption matrix (2026-10-13)

Owner request: `docs/plans/build-harness/00-owner-directive-2026-10-13b.md`. Research was read-only (GitHub at pinned commits), then an Opus pass built the matrix and an independent Opus pass checked it against the repositories; its corrections are at the end and override the table where they disagree.

## Quarantined research clones (owner-approved, nothing installed or executed)

| repo | pinned commit | files | size |
|---|---|---|---|
| activepieces | `c3d66a7f7601` | 30829 | 205 MB |
| claude-ads | `ac2164493391` | 354 | 6 MB |
| harvey | `7940da41b5ac` | 89 | 6 MB |
| n8n | `9052f8ff3e76` | 30875 | 223 MB |
| windmill | `7558ce27a973` | 10815 | 115 MB |

Matrix research pins: ethanplusai/harvey 2b8a7e25a4eec36f7a8213cc1d3ea8ca8b87c5cc (MIT). Not cloned: absent from third_party/upstream and data/quarantine (only _runtime). Not in UPSTREAM_LOCK yet.; AgriciDaniel/claude-ads ac21644933910419529bcf81efb95a9ca71edf81 (MIT). Not cloned. A fabric descriptor must pin this SHA (friday/fabric.py:197 refuses an unpinned upstream in a non-REFERENCE_ONLY mode).

## Components

| repo | component | class | FRIDAY overlap | reason | risks | licence |
|---|---|---|---|---|---|---|
| ethanplusai/harvey@2b8a7e2 | Deterministic pre-send gate (harvey/gate.py pre_send_check) | **EXTRACT_PATTERN** | None for message content. friday/honesty.py:540 'email' ProofShape only checks claims of sends; friday/confirmation.py:56 fingerprint binds an approval to an ac | Pure function, no I/O, no model call: it is the most valuable Harvey part. Re-express it in FRIDAY style, keeping the MIT attribution, as the content gate every staged draft must pass. Add the checks Harvey lacks: unsubs | The English-only banned list gives false confidence. Harvey only calls the gate on the native path (sender.py:464), not on Instantly, so the port must run on ev | yes (MIT) |
| ethanplusai/harvey@2b8a7e2 | Reply opt-out/escalation precedence (handler.py OPT_OUT_PATTERNS ~l.44, escalate ~l.62) | **EXTRACT_PATTERN** | None. FRIDAY has no suppression list. store.py:668 contacts is a personal address book (relation, phone), not a CRM. | Keep the deterministic rule that a keyword opt-out or legal phrase overrides any LLM and writes to suppression. The pilot uses it on a frozen prior-reply fixture so a suppressed lead never gets a draft. Defer LLM intent  | Substring match false-positives on quoted footers. Reply text is attacker-controlled, so any later drafting model must have no tools (Harvey brain.py:68 propert | yes (MIT) |
| ethanplusai/harvey@2b8a7e2 | Cold-email sequence writer (agents/writer.py, prompts/writer.md, skills/email_frameworks.md) | **EXTRACT_PATTERN** | Partial only. friday/fabric_adapters/company_playbooks.py:9,34-35 (sales-ross, a cold-outreach advice skill) returns advice text, not generated drafts. Under ru | Take the 3-step length limits, the evidence-only personalization rule and the JSON parse. Route through friday/model_gateway.py (TokenBudget l.93, budget_for l.121) on the economy route, with no tools. Input is only the  | May invent personal details. Mitigate with a deterministic check that every personalization claim cites a stored evidence id, plus the gate. Prompt injection ar | yes (MIT) |
| ethanplusai/harvey@2b8a7e2 | ICP scoring + heuristic fallback (scout.py _score_contacts l.1105, _heuristic_score l.1182, _title_matches_icp l.1810, _infer_seniority l.1824) | **EXTRACT_PATTERN** | None. No lead scoring exists in friday/. | Make the heuristic deterministic and primary: a score from evidence-backed signals, with auto-low rules (competitor, generic mailbox, student). An optional single LLM 'fit note' merges into the draft call, so a lead cost | Scraped fields can inject into the note, and the note is pasted into the email, so the note must pass the gate. Weights are a business rule the owner must appro | yes (MIT) |
| ethanplusai/harvey@2b8a7e2 | Company signal profiler (collectors/profile.py l.343/l.464, signals.py, integrations/tech_detect.py l.83, skills/signal_playbook.md) | **EXTRACT_PATTERN** | The fetching duplicates friday/netguard.py:320 fetch, friday/toolsets/research.py, scrapling_parse.py:199 and maxun_scraping.py:153-162. The signal rules (AI-cr | This is the AiCodePro 'website / AI-visibility opportunity' detector. Keep only the rule catalog and regex/fingerprint logic, run it on pages FRIDAY fetches via netguard.fetch (timeouts, SSRF refusal) with an honest UA,  | Harvey rotates a disguised UA (profile.py l.187) and ignores robots for its own crawl: drop both. Fingerprint rules go stale. | yes (MIT) |
| ethanplusai/harvey@2b8a7e2 | Outbox approval ladder (agents/sender.py _stage_campaign_native l.348, STOP_STATUSES l.34, kill switch; state.py outbox l.258, approve_outbox l.887) | **EXTRACT_PATTERN** | Approval duplicates friday/confirmation.py:106 Book plus SIGNAL-001 APPROVE (PARTIAL). Policy overlaps user_policy.py customer_email, which defaults to AUTO (us | Take only the staging half: one row per (lead, step) in status pending_review, an idempotency key, stop-on-opt-out, and a paused flag, stored in FRIDAY's store.py. The send half (_drain_due l.420) is REJECTED for the pil | CAN-SPAM gaps in Harvey (no List-Unsubscribe, no postal address). Any future send must bind confirmation to the content digest (ruling 10). | yes (MIT) |
| ethanplusai/harvey@2b8a7e2 | Email finder/verifier, pattern + API verify (integrations/email_finder.py find_email l.554, verify_reoon/zerobounce/hunter) | **REFERENCE_ONLY** | None. | Not needed for a no-send pilot. It spends third-party quota and money. Revisit with a spend envelope (vnext_control.py:55 spend_gate) when sending is approved. Outreach to unverified contacts is blocked per the directive | Keys in env; paid quota. | yes (MIT) |
| ethanplusai/harvey@2b8a7e2 | Raw SMTP RCPT probe (email_finder.py verify_email_smtp l.426, detect_catch_all l.459) | **REJECT** | n/a | An intrusive port-25 probe of third-party mail servers that risks IP blocklisting. | Blocklisting; unreliable on Google/M365. | yes (MIT), rejected on safety |
| ethanplusai/harvey@2b8a7e2 | Business discovery providers (collectors/discover.py OSM l.453, Serper l.404, DataForSEO l.253/l.340) | **REFERENCE_ONLY** | Partial with research.py. Spend would go through spend_gate (vnext_control.py:55). | The pilot uses an owner-authorized company list, so discovery is out of scope. Keep it in the Opportunity Library; a later WRAP must go through spend_gate and confirmation. | DataForSEO requires a $50 deposit; metered spend. | yes (MIT) |
| ethanplusai/harvey@2b8a7e2 | Web-search prospecting (scout.py _search_duckduckgo/_bing/_google l.384-445, directories l.915, _prospect_via_profile_search l.734) | **REJECT** | Duplicates research.py, maxun and scrapling. | HTML-scrapes search engines with UA rotation and captcha evasion (l.68, l.86, l.206, l.217), against their ToS. profile_search harvests LinkedIn people data indirectly, which the owner banned. | ToS violation; personal-data harvesting. | yes (MIT), rejected on policy |
| ethanplusai/harvey@2b8a7e2 | LinkedIn browser automation (integrations/linkedin.py, scout.py l.636, config.py l.96) | **REJECT** | n/a | The owner explicitly banned it (directive b:55, b:103). It logs in with plaintext credentials and persists cookies, and the default yaml enables it. Do not import it, wrap it, or copy its config. | Account ban, ToS, credential exposure. | yes (MIT), rejected on policy |
| ethanplusai/harvey@2b8a7e2 | Job-board hiring signals via python-jobspy (scout.py l.1453) | **REJECT** | The careers-page variant is covered by the extracted profiler. | jobspy scrapes Indeed (ToS-grey), and its licence is unaudited. | ToS; unknown licence. | unknown (jobspy not audited) |
| ethanplusai/harvey@2b8a7e2 | Brain + claude -p heartbeat loop (harvey/brain.py, README loop) | **REJECT** | Duplicates friday/model_gateway.py (FRIDAY's model gateway) and schedule_control.py:31 schedules_create / automation_control.py:45 automations_create (FRIDAY's  | The owner forbids a second controller, a Claude-only brain and continuous model polling (directive b:53, b:97). | Burns Claude quota. | yes (MIT), rejected on architecture |
| ethanplusai/harvey@2b8a7e2 | SQLite state authority (harvey/state.py) | **REJECT** | Duplicates friday/store.py, the one SQLite authority. | A competing SQLite authority is forbidden. Lead, suppression and staged-draft tables are added to store.py instead, with the schema as reference only. | n/a | yes (MIT) |
| ethanplusai/harvey@2b8a7e2 | Senders: Instantly adapter, Gmail API, SMTP providers | **REJECT** | None (no mail tool in friday/; honesty.py:540 only checks claims). | The pilot sends nothing. Instantly reply sending is ungated (handler.py l.309). Any future sender is a separate owner-approved row. | Unapproved external sends. | yes (MIT) |
| ethanplusai/harvey@2b8a7e2 | Web dashboard + vendored fonts (harvey/web, web/fonts) | **REJECT** | Duplicates the FRIDAY UI / Command Center. | A second UI. The Geist/Newsreader fonts ship without a licence file and the root MIT licence does not cover them. | Font licence. | no for fonts |
| AgriciDaniel/claude-ads@ac21644 | Deterministic core: CSV export adapter + Meta native-export profile (claude_ads_core/adapters/csv_export.py, mappings_v1.py, native_export.py, base.py) | **WRAP** | None. friday/toolsets/documents.py:244 documents_extract handles pdf/docx/pptx/xlsx/zip (l.100-221) and has no schema-checked ad-export reader. | Zero runtime dependencies and stdlib only. It has hardened limits (50 MB, 500k rows, duplicate columns and grains rejected). Wrap it in-process behind a fabric adapter pinned to ac21644 (fabric.py:172/197 enforce the pin | Narrow: the Meta native profile maps spend only, and the generic CSV requires 13 columns, so the pilot needs a FRIDAY column map for Ads Manager exports (impres | yes (MIT) |
| AgriciDaniel/claude-ads@ac21644 | Deterministic core: scoring engine (claude_ads_core/scoring.py) | **WRAP** | None. | Pure Decimal functions. Use them for evidence-coverage grading (>=80% graded, 60-79% provisional, <60% insufficient) and partial-bundle rules, through the same pinned adapter. | The health score must stay disabled (meta-health-v1 is disabled upstream). Do not invent weights. | yes (MIT) |
| AgriciDaniel/claude-ads@ac21644 | Contracts and schemas v1 (claude_ads_core/contracts.py, workflow_contracts.py, schemas/v1/*.json) | **WRAP** | Partial. honesty.py:505-545 has typed proof shapes, but no ad-finding schema. | Validate model findings against finding.schema.json and report-bundle.schema.json, so an unvalidated finding never reaches the report. The mutation-plan schema becomes a reference for a later write path. Do not adopt the | The hand-rolled validator may diverge from JSON Schema semantics. Test it against the fixtures. | yes (MIT) |
| AgriciDaniel/claude-ads@ac21644 | ads-meta skill + Meta control catalog (skills/ads-meta/SKILL.md, ads/references/meta-audit.md) | **WRAP** | None in FRIDAY. The Claude-host skill anthropic-skills:meta-ads-strategy-report is outside FRIDAY and not a registry entry. | The cold-start contract and the unknown-when-no-evidence controls are the substantive domain content. Import it as an on-demand FRIDAY skill through friday/skills/lifecycle.py (_require_clean_scan) with SkillSpector over | CAP-MKT-002 is PARTIAL: the reference to meta-audit.md outside the skill dir currently scans as 'partial' and is quarantined until the bounded-reference fix lan | yes (MIT) |
| AgriciDaniel/claude-ads@ac21644 | audit-meta worker agent (agents/audit-meta.md) | **EXTRACT_PATTERN** | The Claude Code agent format duplicates FRIDAY agent_composer/model_gateway dispatch. | Keep the output contract (findings, contradictions, missing_inputs, untrusted-input rule, no scoring by the model) and re-express it as a single model-gateway call on the economy route. Its input is FRIDAY's computed met | Unproven quality, mostly 'unknown' without tracking data. 24 Sonnet turns upstream; capped to one call here. | yes (MIT) |
| AgriciDaniel/claude-ads@ac21644 | Mutation gate policy (ads/SKILL.md, control-plane/manifests/safety-policy.json, mutation-plan.schema.json) | **EXTRACT_PATTERN** | Overlaps policy.py, confirmation.py:56/106 and spend_gate/spend_envelope_store (vnext_control.py:55/69). The enforcement duplicates these. | Take the required fields (exact account and object IDs, before/after diff, blast radius, idempotency key, rollback, verification window, no permanent delete) as the acceptance for any future write path. Enforcement stays | user_policy.py:56-58 defaults ad_campaign_within_envelope to AUTO. An ad-write capability must not inherit that. | yes (MIT) |
| AgriciDaniel/claude-ads@ac21644 | Meta health scoring profile, disabled (control-plane/manifests/scoring-profiles.json) | **REFERENCE_ONLY** | None. | Honour it: the pilot reports findings and evidence coverage, not a health score. | Pressure to invent weights. | yes (MIT) |
| AgriciDaniel/claude-ads@ac21644 | Report renderer (claude_ads_core/reporting.py; OrchestrationStore .claude-ads/runs) | **REFERENCE_ONLY** | Duplicates friday/toolsets/artifacts.py:80, record_delivery.py:19-38 and progress_digest.py:67. | Reuse FRIDAY reporting and evidence (directive b:121). A second artifact root is a duplicate store. Copy only the redaction and escaping ideas. PDF (WeasyPrint, Pyphen/ReportLab licences) is rejected. | n/a | MD/HTML MIT; PDF extras GPL-adjacent, excluded |
| AgriciDaniel/claude-ads@ac21644 | Conductor /ads (ads/SKILL.md, thinking-framework.md, prompt-patterns.md) | **REFERENCE_ONLY** | Duplicates the FRIDAY objective engine (a second orchestrator). | 17.5 KB prompt per session, which conflicts with token discipline and on-demand loading. Its hard-stop examples become negative-control test cases. | Token cost; a second controller. | yes (MIT) |
| AgriciDaniel/claude-ads@ac21644 | Other 11 platform workers/skills, creative agents (copy/visual/format/strategist), ads-create/generate/photoshoot/dna | **REFERENCE_ONLY** | Creative overlaps company_playbooks (marketing-godin) and writing_skill.py (not opened). | Out of pilot scope. Opportunity Library. Load per platform only when a later row needs it. | Image spend via generate_image.py. | yes (MIT) |
| AgriciDaniel/claude-ads@ac21644 | Competitor/research workflows (skills/ads-competitor, ads-research, agents/research-worker.md) | **REFERENCE_ONLY** | Duplicates friday/toolsets/research.py (l.76-551) and the research sidecars. | FRIDAY already owns web research. Not needed for an export audit. | WebFetch-enabled worker. | yes (MIT) |
| AgriciDaniel/claude-ads@ac21644 | Landing-page fetch/analyze/screenshot scripts (scripts/fetch_page.py, analyze_landing.py, capture_screenshot.py, url_utils.py) | **REJECT** | Duplicates friday/netguard.py (check l.233, fetch l.320) and the FRIDAY browser stack. | A second SSRF guard and a second Playwright, which NON_NEGOTIABLES say to avoid (duplicate browsers). | n/a | yes (MIT) |
| AgriciDaniel/claude-ads@ac21644 | Image generation (scripts/generate_image.py) | **REJECT** | n/a | Paid third-party image calls, irrelevant to a read-only audit, and against ruling 15 (no paid API without approval). | Spend. | yes (MIT) |
| AgriciDaniel/claude-ads@ac21644 | Native Claude Code plugin install (/plugin install claude-ads@ai-marketing-hub-claude-ads) | **REFERENCE_ONLY** | Bypasses friday/skills/lifecycle.py and SkillSpector (CAP-MKT-002). | Installs the conductor and all platforms into the build harness, outside FRIDAY's registry and outside the pin. This is an owner decision, not part of the FRIDAY integration. | Unscanned skill in the dev harness; context bloat. | yes (MIT) |

## Licence assessment

Both repos are MIT for the whole tree (Harvey: Copyright 2026 Ethan Rogers; claude-ads: Copyright 2026 agricidaniel, with THIRD_PARTY_NOTICES.md). MIT allows in-process WRAP and pattern extraction. The licence notice and provenance must be kept (CAP-FORGE-013, owner rule 2026-10-03f in docs/plans/capability-forge/ADOPTION_LEDGER.md). Exclusions: (1) the Harvey vendored fonts (web/fonts, Geist/Newsreader) have no licence file and the root MIT licence does not cover them, so do not copy them; (2) claude-ads PDF extras (ReportLab DarkGarden GPL-2.0+ exception, Pyphen GPL/LGPL/MPL via WeasyPrint) are skipped, and markdown/HTML go through FRIDAY artifacts instead; (3) python-jobspy has not been audited and is rejected anyway. claude_ads_core declares zero runtime dependencies (pyproject dependencies = []), so WRAP pulls in no third-party code. Harvey's manifest dependencies (httpx, playwright, bs4, dnspython, aiosmtplib, aiosqlite, pydantic, fastapi, uvicorn) were not licence-audited, but extraction ports no Harvey runtime dependency. No scanner results exist: nothing has been cloned, so SkillSpector and the sandbox qualification have not run. CAP-MKT-002 is PARTIAL (a reference to a file outside the skill scans as 'partial', which the ads-meta skill to meta-audit.md hits) and CAP-MKT-003 is MISSING. Activation of the WRAPped skill is blocked until those land; the pilots may run as TESTING in quarantine.

## Duplicates of existing FRIDAY capabilities

- Harvey brain/heartbeat duplicates friday/model_gateway.py (TokenBudget l.93, billing_class_for l.384) plus schedule_control.py:31 schedules_create and automation_control.py:45 automations_create. REJECT.
- Harvey state.py SQLite duplicates friday/store.py, the single SQLite authority. REJECT. New lead, suppression and staged-draft tables go in store.py. store.py:668 'contacts' is a personal address book with no suppression or lead status, so it is not a CRM.
- Harvey scout/profile HTTP fetching duplicates friday/netguard.py:320 fetch, toolsets/research.py:76-551, scrapling_parse.py:199 and maxun_scraping.py:153-162. Only the signal rules are new.
- Harvey outbox approval duplicates friday/confirmation.py:106 Book plus SIGNAL-001 APPROVE. Only the staging/idempotency pattern is new.
- Harvey writer partially overlaps company_playbooks.py:9,34-35 (sales-ross advice and a cold-outreach skill). This is not behavioural equivalence under ruling 11 (advice vs generated drafts), so the extraction is admissible.
- Harvey web dashboard duplicates the FRIDAY UI / Command Center (A18).
- claude-ads conductor /ads duplicates the FRIDAY objective engine (second orchestrator).
- claude-ads reporting.py + OrchestrationStore (.claude-ads/runs) duplicates friday/toolsets/artifacts.py:80, record_delivery.py:19-38 and progress_digest.py:67.
- claude-ads fetch_page/url_utils guarded_request duplicates friday/netguard.py check l.233 / fetch l.320. Its Playwright scripts duplicate the FRIDAY browser stack.
- claude-ads research-worker and ads-research duplicate toolsets/research.py and the open_notebook/anythingllm sidecars.
- claude-ads mutation-gate prose duplicates policy.py, confirmation.py:56 and vnext_control.py:55/69 spend_gate/spend_envelope_store. Enforcement stays in FRIDAY.
- No FRIDAY duplicate exists for: an ad-export CSV reader (documents.py handles xlsx/pdf/docx/pptx/zip only, l.100-221), ad finding schemas, evidence-coverage scoring, lead scoring, a content pre-send gate, or a suppression list.

## Rejected

- Harvey LinkedIn automation (linkedin.py, scout l.636): banned by the owner (directive b:55, b:103), violates ToS, stores plaintext credentials and cookies, and the default yaml enables it.
- Harvey search-engine HTML scraping + _prospect_via_profile_search: UA rotation and captcha evasion against ToS, and indirect LinkedIn people harvesting.
- Harvey python-jobspy/Indeed path: ToS-grey scraper with an unaudited licence.
- Harvey raw SMTP RCPT probe: intrusive, risks IP blocklisting.
- Harvey brain + claude -p heartbeat: a second controller that burns Claude quota (directive b:53, b:97).
- Harvey state.py SQLite: a competing authority.
- Harvey Instantly/Gmail/SMTP senders and _drain_due: the pilot must not send. Instantly replies are ungated (handler l.309).
- Harvey web dashboard and unlicensed fonts.
- claude-ads landing-page/screenshot scripts: duplicate netguard and the browser.
- claude-ads generate_image.py: paid image APIs, out of scope, ruling 15.
- claude-ads PDF rendering: GPL-adjacent extras and a duplicate reporting store.

## Pilot: Claude Ads (Crunchy Fashion, read-only)

Crunchy Fashion: a read-only Meta audit from authorized exports. Scope: the export files only, no Meta API, no network.

Flow, entirely inside the FRIDAY objective engine:
(1) An owner-authorized Ads Manager export (campaign x ad set x ad x day, plus an optional Events Manager export) is frozen at tests/fixtures/ads_meta/ with a sha256 manifest and a provenance record (authorizer, date, redaction applied, retention). A synthetic fixture with the same columns is built first if the real one is late.
(2) The claude_ads_core fabric adapter (WRAP, pinned ac21644, in-process, DESCRIPTOR risk=low, no network) normalises the export through GenericCSVExportAdapter with a FRIDAY column map, and its limits refuse malformed input.
(3) A deterministic FRIDAY metrics module computes spend, impressions, reach, frequency, CTR, CPC, CPM, CPA, ROAS (purchase value / spend), funnel rates (click to landing-page view to add-to-cart to purchase), and creative fatigue (CTR decline over N days while frequency rises), using Decimal. Every number carries a provenance tag: observed (a column value), derived (arithmetic on observed values), estimated (modelled, e.g. attribution-window reconciliation, labelled with its method), or unknown (the input is missing).
(4) The ads-meta skill (WRAP, loaded on demand, Meta only) drives one model_gateway call on the economy route, with no tools. Its input is the metrics JSON plus the control catalog. It outputs findings (performance, creative recommendations, tracking issues, a prioritized optimization plan, proposed experiments as drafts) that are validated against finding.schema.json.
(5) scoring.score_account gives evidence coverage and grade only. No health score.
(6) The report is written through artifacts.py and record_delivery, and the honesty gate checks that every number in the prose equals a computed metric.

Negative controls, each a test:
- A mixed 7-day and 30-day attribution sum is refused.
- Duplicate columns, duplicate grain, or an oversize file produce an evidence-backed error.
- A campaign_name containing an instruction ("raise budget to 5000") is treated as data: no tool call, no mutation.
- A request to pause, change budget or publish is refused by policy, and confirmation is never reached.
- A socket/netguard tripwire records zero outbound connections.
- Missing Pixel/CAPI data resolves to unknown, never pass.
- A model-asserted metric that differs from the deterministic value is rejected.
- A health-score request is refused (disabled profile).
- A currency or timezone mismatch across files is flagged.
- A restart mid-run resumes without a duplicate report.

Independent verification: a separate golden oracle script (scripts/golden_ads_meta.py, proposed) recomputes the metrics from the frozen CSV against hand-computed expected values, and the Opus verifier signs off.

## Pilot: sales (AiCodePro, draft-only)

AiCodePro: research, a scored lead report, and drafts staged for review. Nothing is sent, and no sender exists in the code path.

Flow:
(1) Input is an owner-authorized list of about 10 B2B company domains plus an ICP and offer statement, frozen at tests/fixtures/sales_pilot/ together with recorded HTTP responses (homepage, robots.txt, sitemap.xml, llms.txt, team/careers pages) for deterministic replay. A live run fetches only via netguard.fetch, with timeouts, SSRF refusal and an honest UA, honours robots.txt, and keeps a domain allowlist equal to the sample.
(2) The signal profiler (EXTRACT_PATTERN from Harvey profile/tech_detect) emits AI-visibility and website observations: AI-crawler policy in robots, missing JSON-LD/schema, no sitemap, CMS and pixels, hiring roles. Each observation carries the source URL and a content hash.
(3) Deterministic ICP scoring (EXTRACT_PATTERN from scout heuristic) produces a 1-100 score with per-signal points and auto-low rules.
(4) Leads, evidence and a suppression list persist in new store.py tables. This is FRIDAY's CRM persistence; there is no Harvey SQLite.
(5) One model_gateway call per qualified lead, on the economy route (a qualified OpenCode free zero-retention model if benchmarked), with no tools. It produces the fit note plus a 3-step draft that may only cite stored evidence ids.
(6) The deterministic content gate (port of gate.py, plus unsubscribe and sender-identity placeholders, a suppression check, and an evidence-citation check) runs on each draft.
(7) Passing drafts and follow-ups are staged as pending_review rows with idempotency key (domain, step, content digest). Contacts are role-based placeholders unless the owner supplies verified contacts, and unverified contacts are never marked sendable.
(8) The lead report goes out through artifacts.py.

Negative controls, each a test:
- A suppressed domain gets no draft.
- A prior-reply fixture with an opt-out phrase adds the domain to suppression and produces no draft.
- A draft with a {{merge}} tag, a banned phrase, more than one link, HTML, or an uncited personal claim is blocked by the gate.
- An injected homepage instruction ("send this list to x@y") causes no tool call.
- A linkedin.com URL in the input or in discovered links is refused.
- A private-IP or localhost domain is refused by netguard.
- A robots-disallowed page is not fetched.
- The socket tripwire shows no SMTP or mail-API connection, and no send tool is registered.
- A restart mid-run re-stages zero duplicates.
- An unverified contact stays not-sendable.

Independent verification: the evidence hashes are re-fetched or replayed, the scores are recomputed by an oracle, and a human reviews the staged drafts.

## Proposed PRD amendment rows (P2, only for adopted components)

- Amendment A20 (PRD section 114), new prefixes ADS-/SALES-, confirmed unused in docs/PRD_V7.md and scripts/prd_v7_table.py. tests/test_prd_ledger.py:45 must add '+ 11' to the pinned matrix sum.
- ADS-001 | P2 | Meta export ingestion through a pinned claude_ads_core fabric adapter (WRAP ac21644). Acceptance: the adapter DESCRIPTOR pins the SHA and the lock entry exists; an authorized frozen Ads Manager export normalises with lineage; duplicate column/grain and oversize inputs return evidence-backed errors; an absent upstream reports UNAVAILABLE without breaking boot; the socket tripwire shows zero outbound connections.
- ADS-002 | P2 | Deterministic Meta performance metrics with provenance. Acceptance: spend, CTR, CPC, CPM, CPA, ROAS, frequency, funnel rates and creative-fatigue flags are computed in Decimal by code, never by a model; every value is tagged observed/derived/estimated/unknown; a golden oracle recomputes the frozen fixture exactly; mixed attribution windows are refused, not summed.
- ADS-003 | P2 | On-demand ads-meta audit skill producing schema-valid findings (WRAP SKILL.md + meta-audit.md, EXTRACT_PATTERN audit-meta contract). Acceptance: the skill passes SkillSpector over its hash-bound closure (ruling 12) and the CAP-MKT-003 sandbox before ACTIVE; only the Meta platform loads; one model_gateway call with no tools; every finding validates against finding.schema.json; missing tracking evidence yields unknown; a model number that differs from ADS-002 is rejected; there is no health score while meta-health-v1 is disabled.
- ADS-004 | P2 | Ad-account mutation stays refused; any future write meets the mutation-plan contract (EXTRACT_PATTERN). Acceptance: a pause/budget/publish/audience request is refused by policy.py with an audit record; a future write capability must carry account+object ids, a before/after diff, an idempotency key, rollback, post-state verification and confirmation bound to the content digest; ad writes never inherit ad_campaign_within_envelope AUTO.
- ADS-005 | P2 | Crunchy Fashion read-only audit journey. Acceptance: on authorized frozen exports FRIDAY produces performance findings, creative recommendations, tracking issues, a prioritized plan and draft experiments, with observed vs estimated labelled; all ten listed negative controls pass; restart resumes without duplicates; independent oracle and verifier sign-off; tokens and cost per verified run are recorded.
- SALES-001 | P2 | Company signal profiler for website and AI-visibility opportunities (EXTRACT_PATTERN Harvey profile.py/tech_detect.py, MIT attribution). Acceptance: fetches go only through netguard.fetch with an honest UA, timeouts and a domain allowlist; robots.txt is honoured; every observation cites the URL and content hash; LinkedIn and private-IP targets are refused; the frozen replay fixture gives identical output.
- SALES-002 | P2 | Deterministic ICP lead scoring with evidence (EXTRACT_PATTERN scout heuristic). Acceptance: the score is computed by code from stored signals with per-signal points and owner-approved weights; auto-low rules (competitor, generic mailbox, student) are tested; an oracle recomputes the scores; leads and evidence persist in store.py, with no second SQLite.
- SALES-003 | P2 | Evidence-grounded outreach drafts (EXTRACT_PATTERN writer.py). Acceptance: one model_gateway call per lead on the economy route with no tools and paid-API fallback refused; drafts cite only stored evidence ids; injected page instructions produce no tool call; tokens per verified lead are recorded.
- SALES-004 | P2 | Deterministic pre-send content gate (EXTRACT_PATTERN gate.py). Acceptance: blocks unrendered merge tags, banned phrases, more than one link, HTML, over-length text, uncited personal claims, suppressed recipients, and a missing unsubscribe or sender-identity block; a test for each reason; the gate runs on every staging path.
- SALES-005 | P2 | Staged outreach queue with suppression and opt-out in store.py, and no sender (EXTRACT_PATTERN sender.py staging + handler.py opt-out precedence). Acceptance: drafts and follow-ups are stored as pending_review with an idempotency key; an opt-out phrase overrides any model and writes suppression; a restart creates zero duplicate rows; no send tool is registered and the socket tripwire shows no mail connection; unverified contacts are never sendable.
- SALES-006 | P2 | AiCodePro research-to-draft journey. Acceptance: on the authorized frozen sample FRIDAY produces a scored lead report with verifiable sources, CRM persistence and staged drafts for human review; all ten listed negative controls pass; nothing is sent; independent verification and cost per verified lead are recorded.

## Model and cost plan

Deterministic code, with zero model calls, does ingestion, metrics, coverage scoring, signal extraction, lead scoring, the content gate, suppression, staging, fetching and scheduling (FRIDAY schedules/automations, event-triggered, no heartbeat).

Model calls:
- Ads: one model_gateway call per audit on the economy route, input capped by TokenBudget (friday/model_gateway.py:93,121), metrics JSON only, Meta skill only, no conductor.
- Sales: one call per qualified lead (fit note and draft merged), economy route. Use a benchmarked OpenCode free zero-retention model where it is qualified, Codex/subscription otherwise. Sonnet only if a measured quality gap justifies it. Opus only as the independent verifier.
- Paid API fallback is never used (ruling 15).

Rough estimate, not measured: ads about 6-10k input and 2k output tokens per audit; sales about 2-3k input and 600 output per lead, so about 30k tokens for a 10-lead pilot.

CI and fixture tests use recorded model outputs, so they spend zero tokens. Cost per verified completed task is recorded in the existing ledger and fabric_outcomes.

Build work: Sonnet/Codex builders (about 3 slices: adapter+metrics, sales extraction, pilots), one Opus verification. Harvey's claude -p loop is eliminated, so its quota cost is zero.

Not done: no clone, scan, pytest or run was performed. Harvey's analyst agent and the truncated tail of its research were not inspected. Risk: user_policy AUTO defaults for customer_email and ad campaigns must be fixed before any send or write row lands.

## Owner decisions needed

- Approve cloning harvey@2b8a7e2 and claude-ads@ac21644 into the CAP-FORGE-003 quarantine (friday/quarantine.py, AppContainer), adding UPSTREAM_LOCK entries, and running SkillSpector there. Nothing is cloned yet.
- Accept that the pilots run as TESTING (quarantine/fixtures) and cannot reach ACTIVE while CAP-MKT-002 (PARTIAL, out-of-skill reference) and CAP-MKT-003 (MISSING sandbox) are open. Confirm the P2 priority behind the P0 burn-down (directive b:7, b:49).
- Crunchy Fashion data: supply authorized Meta Ads Manager (and Events Manager) exports, with provenance, consent, redaction and retention for committing them as frozen fixtures. Alternatively approve a synthetic fixture with real column names until they arrive.
- AiCodePro sample: the list of companies, the ICP and offer statement, the lawful basis for processing (jurisdictions, GDPR legitimate interest), and approval of the scoring weights (a business rule).
- Sender identity for a later send phase: sending domain, display name, postal address, unsubscribe mechanism, daily caps. Pilot drafts carry placeholders only.
- Policy defaults: friday/user_policy.py:56-58 sets customer_email, social_publish and ad_campaign_within_envelope to AUTO, and first_run.py:122-130 grants customer_email and social_publish. No enforcement outside vnext_control.py and first_run.py was found. Approve a separate cold_outreach / ad_account_write domain that defaults to CONFIRM (never AUTO), or rule otherwise. This is a security gap for any future send.
- Whether to install the claude-ads plugin directly in Claude Code (directive b:183-194). That bypasses FRIDAY's SkillSpector and skill lifecycle and loads the conductor plus 12 platforms. Recommendation: not until it has been scanned.
- Is there an external CRM (HubSpot, etc.) to integrate? None exists in FRIDAY. The default is new store.py tables.
- Paid discovery and verification APIs (Serper, DataForSEO, Hunter, Reoon, ZeroBounce) are out of the pilot. Approve them later under a spend envelope.
- Confirm amendment number A20 and the ADS-/SALES- id prefixes.

## Independent verification (corrections override the table)

CONFIRMED, checked at the pinned SHAs (harvey 2b8a7e25, claude-ads ac216449)
- Licences: both repos are MIT (LICENSE, 2026). claude_ads_core has no runtime dependencies (pyproject.toml `dependencies = []`).
- Harvey loop: a heartbeat every 15 minutes (README.md:87, main.py:122/157). It calls `claude -p` (brain.py:60) with `--tools ""` and `--strict-mcp-config` (brain.py:68-69).
- LinkedIn: Playwright automation with an email and password (linkedin.py:39), cookies saved to disk (l.22/154), on by default (config.py:97, harvey.yaml:56).
- gate.py imports only re and dataclasses. It is called only in `_drain_due`, the native path (sender.py:464).
- Opt-out and escalation run before the model is asked (handler.py:210-215).
- Claude Ads is read-only by default (safety-policy.json `default_mode`, ads/SKILL.md:215), lists 7 mutation gates, and refuses permanent deletion.
- Claude Ads limits: 50 MB, 500k rows and 13 required columns (csv_export.py:18-34). The Meta export maps spend only (mappings_v1.py:178-183). Coverage thresholds are 80/60 (scoring.py:18-19). meta-health-v1 is disabled. ads/SKILL.md is 17,563 bytes. audit-meta.md runs Sonnet with maxTurns 24.
- All Harvey line numbers I checked match. In FRIDAY, honesty.py:540, confirmation.py:56/106, user_policy.py:56-58, store.py:668, netguard.py:320, model_gateway.py:93/121 and fabric.py:172/197 also match. friday/ has no suppression list.

WRONG
1. Wrong paths: vnext_control.py, schedule_control.py and automation_control.py are under friday/tools/. record_delivery.py and progress_digest.py are under friday/. scrapling_parse.py and maxun_scraping.py are under friday/fabric_adapters/, where :199 and :153-162 are DESCRIPTORs, not fetch code.
2. company_playbooks.py:34-35: "sales-ross" is one of the EXECUTIVES role briefs, not a cold-outreach skill. The cold-outreach skill is a separate item in the skill list (l.9).
3. The claim that Harvey polls the model continuously is wrong. decide_next_action (main.py:73-106) is deterministic, and the model is called only when an action runs.
4. Opt-out: Harvey has no suppression table. It sets the prospect status to "opted_out" (handler.py:257).
5. Claude Ads mutation: the gate is also enforced in code. BaseAdapter raises MutationDisabledError for draft, apply, verify and rollback (base.py:96-115).
6. PDF licences: WeasyPrint and ReportLab code are BSD-3. The copyleft parts are Pyphen (GPL/LGPL/MPL, your choice) and ReportLab's DarkGarden font (THIRD_PARTY_NOTICES.md). reporting.py uses only WeasyPrint. OrchestrationStore is in claude_ads_core/__init__.py.
7. Fonts: no licence file is shipped (README.md:335 says only "vendored"). Change "no" to "unverified".

Matrix corrections: items 1–7 above. Every classification stands.

Not checked: the truncated last component, CAP-MKT-002's actual scan result, robots handling beyond profile.py:385-394, jobspy's licence, and gate.py's English-only list.

## Addendum 2026-10-13 22:30 — real SkillSpector scan (pinned scanner, --no-llm)

Scanner: NVIDIA SkillSpector from `third_party/upstream/skillspector` (UPSTREAM_LOCK pin `c7958a3`), installed into the
isolated tool venv `data/tools/skillspector-venv` (Python 3.12). Pattern mode only (`--no-llm`, no model calls), run over the
quarantined clones. Raw reports: `data/quarantine/research/skillspector/<repo>.json`.

| repo @ commit | findings | HIGH / MEDIUM / LOW | top categories | risk assessment |
|---|---|---|---|---|
| claude-ads @ `ac2164493391` | 84 | 12 / 69 / 3 | Dangerous Code Execution 35, Agent Snooping 26, Data Exfiltration 9, Excessive Agency 8 | score 100, CRITICAL, **DO_NOT_INSTALL** |
| harvey @ `7940da41b5ac` | 92 | 52 / 29 / 11 | Privilege Escalation 45, Dangerous Code Execution 11, Data Exfiltration 11, Tool Misuse 9, Supply Chain 3 | score 100, CRITICAL, **DO_NOT_INSTALL** |

Reading: pattern mode over-reports (several HIGH hits point at `.gitignore`, `CHANGELOG.md` and `.env.example` lines), but under
the owner's fail-closed rulings (CAP-MKT-002: a clean score never overrides incomplete coverage) neither repository may be
installed or activated, and the optional direct `/plugin install claude-ads` stays off. Adoption remains EXTRACT_PATTERN /
REFERENCE_ONLY as the matrix says; any WRAP component must be re-scanned on its own resolved closure before it can leave TESTING.
