# Skill security

A skill is text that a model reads. That is exactly why it is dangerous:
text can ask for anything. The invariants below are enforced in code and
each has a test in `tests/test_skills_golden.py` or
`tests/test_skills_units.py`.

| Invariant | Enforced by | Test |
|---|---|---|
| A skill cannot mark itself verified, trusted or low-risk | lint `self_verification` FAIL on `metadata.friday.{state,verified,quality,trust,risk,last_verified}`; risk is classified from the body | SK-10 |
| Verification is evidence produced outside the skill | `registry.evaluation_on_record` (nonce + hash + version) before any ACTIVE | SK-02, SK-10 |
| Agent-created and downloaded content starts in quarantine | `lifecycle.register` by trust | SK-02, SK-05, SK-10 |
| Unsafe instructions never activate | lint FAIL: prompt injection, policy bypass, destructive commands, remote code execution, exfiltration, self-authorization, hidden Unicode | SK-05, SK-10, units |
| Secrets never activate and never leave | lint `secret` FAIL (OpenAI/Anthropic, AWS, GitHub, Slack, Google keys, private-key blocks, LiveKit); `possible_secret` WARN for literal assignments | SK-05 |
| Private data never leaves the machine | `private_data` is WARN locally and FAIL `for_publication` (profile paths, e-mail, internal addresses) | units |
| Codebase knowledge is never public | `publisher.refusals`: scope PROJECT/REPOSITORY → refused for PUBLIC_GIT | SK-08 |
| Public publication needs standing authorization or an approval; silence is a refusal | `PublishPolicy.allow_public` or an approver that returns True; a failing/absent approver is a no | SK-08 |
| High-risk classes never activate through an agent path | evaluator `risk` FAIL for DESTRUCTIVE / SECURITY_SENSITIVE / FINANCIAL / ACCOUNT_CONTROL with trust `agent` | SK-10 |
| A skill's declared permissions grant nothing | `SkillRecord.permissions` is informational; FRIDAY's policy engine decides every action at call time | by construction |
| Kernel paths stay untouchable | skills are never code; `friday.self_upgrade.KERNEL_PATHS` governs code changes | existing invariants |
| Supporting files stay in place | `write_file` refuses `..` and anything outside `references/ templates/ scripts/ assets/` | units |
| Every state change is attributable | `skill_history` rows with actor and reason; illegal moves refused | units |

## What lint scans

`friday.skills.lint` runs the same scanners over `SKILL.md` and every
text file of the skill (binary assets are skipped):

- **unsafe**: "ignore previous instructions", "disable/bypass the policy
  or approval", `rm -rf /`, `format c:`, force-push without a lease,
  `curl … | sh`, `Invoke-Expression (DownloadString…)`, "send the api
  key to…", "grant yourself permission"; zero-width and bidi characters.
- **secrets**: well-known token shapes (FAIL); generic `key = <literal>`
  assignments (WARN) unless the value is an obvious placeholder.
- **private**: profile paths, e-mail addresses, loopback/LAN addresses.
- **incident noise**: dates, run ids, clock times (WARN; the curator
  removes them).

Hermes's own `skills_guard` still runs on anything installed from a hub;
the two scanners overlap on purpose.

## What lint does not do

It does not judge whether a procedure is *right*; the evaluator's
procedure tests and the owner do. It does not read the skill's own
opinion of itself; those keys are refused.
