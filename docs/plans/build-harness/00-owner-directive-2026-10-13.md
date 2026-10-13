# Owner directive 2026-10-13 — Owner Rulings 6–22

Source: the owner's reply in the build-controller session on 2026-10-13, answering the controller's
blocker list items 6–22. Recorded verbatim below. The only change is that web-citation rendering
artefacts (favicon image links, "GitHub +2"-style source chips, "&#x20;" entities and a
"Pasted text (2).txt" attachment label) were removed. These rulings bind: quote them, never
paraphrase them. Ledger rows carry them as `RULING 2026-10-13 (owner, docs/plans/build-harness/00-owner-directive-2026-10-13.md): ...`.

---

# FRIDAY — Owner Rulings 6–22

Decision: Keep FRIDAY quality-first, cost-efficient and fail-closed on security. Do not weaken benchmarks simply to mark requirements VERIFIED. Reuse the existing kernel instead of introducing another scheduler, router or sandbox framework.

I've checked your uploaded v7 requirements ledger against current upstream documentation. These rulings are approvals for your development harness to act within the stated scope; they are not claims that the changes have already been implemented.

## Rulings 6–14: Intelligence, skills and orchestration

| #  | Requirement   | Owner ruling                                                                                                                                               |
| -- | ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 6  | COG-002       | Keep 0.90 and 1:3:5 provisionally. Replace difficulty-label-only evaluation with real measured model outputs before verification.                          |
| 7  | MODEL-003     | Accept the versioned evaluation report. Defer learned routing. Keep the simple router; do not build a harder dataset just to justify a neural router.      |
| 8a | EVO-004       | All self-noticed improvement triggers must pass the Evolution Value Gate. No exemption for autonomous experiments.                                         |
| 8b | EVO-004       | Use source-specific triggers. Upstream-lock for repository releases, model-catalog watcher for models, INTAKE-001 for newly submitted repos.               |
| 9  | SKILL-005     | No blanket exemption for built-in or owner skills. Verified unchanged legacy skills may remain active; new/changed skills need evidence before activation. |
| 10 | SIGNAL-001    | Yes, unify durable run-control and continuity through the signal bus. No, never approve a targetless parked action.                                        |
| 11 | CAP-FORGE-011 | Compare actual behavior, not operation names. Partial overlaps remain inactive unless the non-overlapping functionality is measurable and worthwhile.      |
| 12 | CAP-MKT-002   | Allow bounded references to pinned repository files while retaining strict scanning. No scanner bypass for external, missing or uninspectable references.  |
| 13 | CTX-002       | Keep R\@1 ≥ 0.90 and R\@3 ≥ 0.97. Current results remain PARTIAL.                                                                                          |
| 14 | BIN-002       | Confirmed. Parse bytes inside quarantine; never execute the specimen.                                                                                      |

### 6. COG-002 — What the numbers mean

Keep the current values as benchmark assumptions, not proven production facts:

- `QUALITY_THRESHOLD = 0.90`: a minimum measured success criterion on the evaluation suite, not a subjective confidence score.
- `PRICE_WEIGHTS = 1:3:5`: normalized simulation costs for economy, standard and deep reasoning until actual provider usage is available.
- Human difficulty labels: useful for stratifying tasks, but not sufficient as a quality oracle.

Require actual successful-task measurement, including retries and verification cost. Also correct the classifier issue recorded in the ledger: a destructive-looking instruction such as `remove` must be classified using its target and consequences, rather than just its verb.

The ledger explicitly says COG-002 currently relies on modeled prices and human minimum-tier labels rather than measured outputs.

### 7. MODEL-003 — Avoid expensive research without a benefit

I approve closure of the existing versioned evaluation report deliverable, subject to checking that its cited report and tests are valid.

Preserve the stronger future requirement separately: any learned router must beat the deterministic baseline on held-out quality and cost tests before promotion.

Do not mark an unbuilt learned router as verified. More complex routing isn't automatically better.

### 8–9. Evolution and skill activation

The Evolution Value Gate applies to self-noticed problems, new model releases, repository updates, repeated errors and proposed self-improvements.

Detection itself may be cheap and event-driven. Spending money or deploying a candidate requires quantified expected value and policy admission.

For skills, distinguish existing verified capability from a new candidate. An unchanged, previously verified built-in skill shouldn't suddenly become unusable because its historical outcome record is incomplete. But an untested skill must not gain ACTIVE status simply because the creator is FRIDAY or the owner.

A new capability needs reproducible functional evidence; a meaningful upgrade needs a measurable benefit or a demonstrated gap.

### 10–12. Signals, overlaps and scanner references

For SIGNAL-001, make STOP, RESUME, APPROVE and AMENDMENT durable commands handled by the existing objective authority. Broadcast events are notifications of authoritative state changes, not a second control plane.

A web approval must bind to a specific objective, action, permission scope, content digest and expiry. No target, no approval.

For CAP-FORGE-011, two capabilities count as behaviorally equivalent only when representative input/output contracts, semantics, side effects, error behavior and permission requirements match. Matching tool names is insufficient. Partial overlaps should remain `REFERENCE_ONLY` unless a separately useful operation or measured improvement justifies admission.

For SkillSpector, scan the `SKILL.md` and the resolved, pinned, permitted dependency closure. Relative repository references are acceptable when they stay within approved roots, are hash-bound and fully inspected. Path traversal, unresolved symlinks, unbounded transitive references and uninspected executable dependencies remain blocked.

NVIDIA documents transitive-reference inspection and strict incomplete-scan detection. A clean risk score cannot override incomplete coverage.

### 13–14. Retrieval and binary analysis

Do not lower CTX-002's accuracy requirement from 0.90/0.97 to fit the current 0.45/0.70 results. Improving tool discovery while preserving its substantial context reduction is the work. Test lexical, semantic and hybrid ranking against an untouched holdout and report recall separately from cost.

For BIN-002, confirm the precise boundary:

The untrusted EXE/DLL is opened read-only and parsed inside an OS-enforced quarantine. No loader invocation, sample execution, imports resolved through execution, external network fetches or host command spawning. Unsupported or malformed inputs return evidence-backed errors.

## Rulings 15–21: Billing, GitHub, Windows and live execution

| #  | Item                              | Owner ruling                                                                                                                                            |
| -- | --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 15 | Paid API keys behind `/model`     | NO by default. Only allow a separately approved, visibly billable profile with explicit spending and data-policy limits.                                |
| 16 | Push v7 work to GitHub and run CI | YES, conditionally. Push only reviewed, secret-free commits to a named feature branch. Run CI; no direct push to protected `main` or automatic release. |
| 17 | EXEC-SBX-001 symlink test         | YES. Run once with Developer Mode enabled, preferably without elevating the entire test process.                                                        |
| 18 | PKG-001 Windows Sandbox           | YES. Enable on a supported PC, or use a clean VM. Don't restart the host while development jobs are active.                                             |
| 19 | HARN-001 `allow_live`             | YES, for one bounded Hermes test session only. No standing live authority.                                                                              |
| 20 | SANDBOX-001 / ORCH-008            | AppContainer for qualified lightweight jobs; Windows Sandbox or VM for high-risk labs.                                                                  |
| 21 | WF-001 external workflow engines  | YES to pinned research clones. Execution and embedding have separate approval and licensing gates.                                                      |

### 15–16. Spending and GitHub

For `/model`, expose subscription-backed or qualified free routes by default. If a user later opts into a paid API key, display the actual billing provider, spending limit and privacy policy. Do not silently substitute a billable endpoint when Codex or Claude exhausts its plan allowance.

For GitHub, approval covers a dedicated feature branch and CI run, not pushing all dirty files or merging everything.

Before pushing, the harness must check the staged diff, exclude credentials and local databases, validate the destination remote, run applicable local tests and record the commit. GitHub's push protection is valuable but not a substitute for reviewing the contents.

### 17. EXEC-SBX-001 — Symlink escape test

Microsoft confirms that Windows Developer Mode can permit non-administrative symbolic-link creation on supported Windows versions.

My preference is Developer Mode on a dedicated development/test machine, rather than running the whole test suite as Administrator.

Run from the repository root after enabling it:

```
.venv-build\Scripts\python.exe -m pytest tests/test_scoped_runner_escape.py -q
```

The result must show genuine OS-enforced denial of escape attempts. Running elevated must not accidentally grant the tested workload additional access. Preserve logs and classify any host-specific unsupported case honestly.

### 18. PKG-001 — Clean-machine installation

Microsoft documents this administrator PowerShell command:

```
Enable-WindowsOptionalFeature -FeatureName "Containers-DisposableClientVM" -All -Online
```

Save work first; Windows may require a reboot. Windows Sandbox requires a supported Windows edition and hardware virtualization.

Use a clean VM on another machine if this laptop lacks the resources. Test actual EXE installation, launch, useful task execution, persistence, restart and cleanup—not simulated paths.

For untrusted tests, configure networking and clipboard sharing explicitly because Windows Sandbox enables them by default.

### 19. HARN-001 — One live Hermes session

Approve `allow_live=true` only inside the specific HARN-001 test invocation.

The session may validate deployment of the generated FRIDAY profile, tool discovery, model response and a permitted test action. It must use scoped credentials, a bounded execution budget, synthetic data and a restorable configuration snapshot.

After the test, the live authorization expires. It must not become a persistent bypass around FRIDAY's existing policy or model budgets.

### 20. Sandbox runtime selection

Use the existing K1/K2/K3 abstraction:

| Tier | Selected runtime                             | Suitable work                                                                     |
| ---- | -------------------------------------------- | --------------------------------------------------------------------------------- |
| K1   | Windows AppContainer/LPAC                    | Qualified low-risk skill execution and lightweight parsing                        |
| K2   | WSL2 with a verified policy-enforced sandbox | Coding, building and testing where required isolation is proven                   |
| K3   | Windows Sandbox or clean VM                  | Untrusted binaries, dangerous dependencies, higher-risk Evolution Lab experiments |

A higher tier is chosen based on required security properties, not simply task complexity. If a selected backend cannot enforce the execution envelope, fail closed.

### 21. Workflow-engine repository adoption

The license distinction is important:

| Repository                                                   | Research clone | Product integration                                                                       |
| ------------------------------------------------------------ | -------------- | ----------------------------------------------------------------------------------------- |
| [Activepieces](https://github.com/activepieces/activepieces) | Approved       | MIT-licensed core may be considered with notices; enterprise modules separately licensed  |
| [n8n](https://github.com/n8n-io/n8n)                         | Approved       | Research or permitted internal use; no assumption of resale/embedding rights              |
| [Windmill](https://github.com/windmill-labs/windmill)        | Approved       | Internal isolated use or reference; AGPL/commercial terms require review before embedding |

Activepieces confirms its MIT core and commercial enterprise modules. n8n's Sustainable Use License restricts use mainly to internal business and noncommercial scenarios. Windmill explicitly distinguishes internal use, AGPL source builds and commercial redistribution or embedding.

Approve pinned, read-only clones with a license manifest and dependency inspection. Do not install all three as active workflow engines. Benchmark one narrow workflow problem against FRIDAY's existing system before admitting any runtime.

## 22. Remaining operational decisions

| Item                           | Final ruling                                                                                                                 |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| Code-signing certificate       | Defer purchasing until the clean EXE is validated. Research a conventional OV certificate or Microsoft Store distribution.   |
| Long real-use soak             | Approve a staged, resource-bounded soak after core gates pass: short smoke test, then a 12–24-hour run.                      |
| `--stop` meaning               | Graceful stop: admit no new work, preserve state and stop at a safe checkpoint. Emergency termination is a separate command. |
| Hermes OpenRouter/Codex limits | Use reported usage windows and real provider errors, not guessed reset times.                                                |
| K1 own-repo roots              | Permit only explicitly bound working roots; no broad home-directory or sibling-repository access.                            |
| GBrain installation            | Approve one isolated, local-only, benchmarked memory pilot; not replacement of FRIDAY's canonical brain.                     |
| Power plan / Defender          | Balanced power, thermal-aware concurrency, no broad Defender exclusions.                                                     |

### Code signing — an important finding

Microsoft's current Artifact Signing Public Trust requirements list eligible countries, but India is not on the published list. If your signing publisher is established in India, don't assume the approximately $9.99/month Azure option will be available.

Microsoft also describes conventional OV certificates as a worldwide option, and Microsoft Store distribution as another route for suitable apps.

No certificate purchase is authorized by these rulings. Produce a short vendor/eligibility comparison first; continue unsigned testing in controlled environments.

### Hermes rate limits

The latest Hermes documentation includes:

```
hermes usage --provider openai-codex --json
hermes usage --provider openrouter --json
```

It also documents provider-specific handling of 429, 401, quota exhaustion and reset timestamps. An exhausted Codex subscription should never be reported as missing credentials merely because the provider returned a 429.

### GBrain

The [GBrain project](https://github.com/garrytan/gbrain) supports a Bun-based local memory setup, including a keyless PGLite configuration. Its maintainers explicitly warn that the unrelated npm package named `gbrain` is not their project.

Use an isolated, pinned source checkout. First compare its recall accuracy, latency, token overhead, privacy and memory correction behavior against FRIDAY's existing shared brain. Only promote a measured improvement.

### Thermal management

Use Balanced power as the ordinary setting; reduce concurrent workers, local-model residency and CPU pressure when the machine heats up.

Microsoft recommends Defender performance mode on a properly configured Windows 11 Dev Drive as a safer development-performance approach than broad folder exclusions. It still keeps real-time protection enabled.

Do not exclude repository roots, downloaded dependencies, skill sandboxes or model-generated executables from Defender.

## Copy-paste owner directive

# FRIDAY — OWNER RULINGS 6–22

These decisions are approved for implementation in the existing FRIDAY architecture.

## Intelligence and evaluation

- **COG-002:** Retain 0.90 measured quality threshold and provisional 1:3:5 normalized price weights. Human difficulty labels are benchmarks, not proof of model success. Use actual verified outputs before closure.
- **MODEL-003:** Accept the existing versioned evaluation-report deliverable after validating its evidence. Defer learned-router implementation to a separate future requirement. Keep the deterministic baseline.
- **CTX-002:** Retain R\@1 ≥ 0.90 and R\@3 ≥ 0.97. Do not lower thresholds to close the row. Improve retrieval on untouched holdout tests.

## Evolution and skills

- **EVO-004:** All autonomous improvement experiments, including self-noticed triggers, pass the Evolution Value Gate before paid execution. New upstream releases are detected by upstream-lock; model releases by a catalog watcher; manually supplied repositories by INTAKE-001.
- **SKILL-005:** No permanent trust-based exemption. Existing verified unchanged skills may remain active. New/changed skills require scanner clearance and measured functional outcomes before activation.
- **CAP-FORGE-011:** Behavioral equivalence requires matching contracts, side effects, error behavior and permission requirements. Default partial overlaps to REFERENCE_ONLY unless distinct useful behavior or measured improvement is demonstrated.
- **CAP-MKT-002:** Permit pinned, bounded repository references in SKILL.md only when resolved contents and dependency closure are fully scanned. Preserve strict fail-closed behavior.
- **BIN-002:** Read-only byte parsing inside OS-enforced quarantine; never execute the binary.

## Signals and permissions

- **SIGNAL-001:** Route durable run-control, continuity and amendments through the authoritative objective signal bus. No approval of targetless parked actions.
- **Paid API behind /model:** Disabled by default. Explicit opt-in, visible billing, approved privacy and bounded spend required.

## Execution and infrastructure

- **EXEC-SBX-001:** Run the symlink escape test once with Developer Mode on a qualified host, or use a controlled elevated test where necessary. No permission weakening.
- **PKG-001:** Real Windows Sandbox or clean VM verification required. Simulation is insufficient. Enable/reboot only when active jobs are safely parked.
- **HARN-001:** Approve one scoped live Hermes session to validate profile deployment. No standing allow_live permission, unauthorized spending or permanent privileged access.
- **SANDBOX-001 / ORCH-008:** K1 AppContainer/LPAC for qualified lightweight jobs; K2 policy-enforced WSL2 environment when proven; K3 Windows Sandbox or VM for high-risk laboratory work. No unrestricted fallback.
- **WF-001:** Approve pinned research clones of Activepieces, n8n and Windmill. No automatic activation, commercial embedding or license-incompatible redistribution.

## Other operational rulings

- Code signing: benchmark eligible certificate/distribution options; no purchase yet.
- Soak: staged, 12–24-hour bounded real-use test after core release gates.
- `--stop`: graceful stop at safe checkpoint, persist work, no new dispatch. Keep emergency terminate/kill as a distinct action.
- Hermes rate limits: use authoritative quota and reset data; verify account health before retrying.
- K1 roots: explicitly scoped worktree read/write access; no broad host, credentials, sibling repositories or parent folders.
- GBrain: one pinned, local-only isolated memory experiment after Bun qualification; never replace canonical memory without comparative evidence.
- Thermals: Balanced power, resource-based throttling; no broad Microsoft Defender exclusions.

## GitHub authorization

Approve a push to a dedicated feature branch and a CI run only after:

1. Exact diff and remote are reviewed.
2. No secrets, personal data, local databases or unapproved generated artifacts are included.
3. Existing tests and release guards are not weakened.
4. Known failures and missing environment tests remain explicitly reported.
5. Commit SHA, branch, CI results and rollback path are recorded.

No direct push to protected main, automatic merge, or public release.

## Execution instruction

Audit current HEAD and the latest requirement ledger before changing code.

Apply the decisions to the authoritative owner-directive and requirement records without inventing new architecture.

Work in the smallest safe slices. Do not interrupt recoverable active work. Prioritize P0 execution, sandbox and continuity gates over research-only improvements.

For every completed change provide:

- Requirement ID
- Exact files modified
- Tests and negative controls
- Evidence or blocker
- Actual status
- Resource and cost impact
- Next smallest action

Do not mark unperformed live tests, unsupported provider behavior, or missing hardware qualification as VERIFIED.

**Goal: FRIDAY becomes more reliable and more cost-efficient while preserving autonomous development, continuity, security and real-world verification.**

The immediate sequence I recommend is validate the symlink escape test, complete the scoped Hermes session, safely push the reviewed branch for CI, and then resume the real clean-machine testing when the Windows environment is ready. Everything else can remain in the existing dependency-ordered queue.

---

## Controller notes (2026-10-13, not owner text)

- EXEC-SBX-001: on 2026-10-13 Developer Mode was off on this host (`AllowDevelopmentWithoutDevLicense` unset; `mklink` refused with "You do not have sufficient privilege"). The test waits for the owner to switch Developer Mode on.
- PKG-001: Windows Sandbox is not enabled; enabling it may reboot the host, so it waits until build jobs are parked.
- GitHub: the integration branch `prd-v7/slice-7-adaptive-hardware` has no upstream; `origin` is `github.com/DarshPro07/F.R.I.D.A.Y`, whose `main` is still 3c43a2d (2026-09-20). A push would create a new feature branch; `main` is untouched.
