---
description: "Treat user input, secrets, and dependencies with security in mind. No new OWASP top-10 holes."
globs: "*"
alwaysApply: true
---

# Rule 12: Security & VAPT

**When this applies:** any team that handles user data, authentication, payments, or external traffic. Optional for purely internal scripts.

Every change is a potential attack surface. Default to safe.

## Boundaries

- **All user input is untrusted** — validate types, lengths, encodings, allowed characters. Do not trust client-side validation.
- **All secrets live in env vars or a secret store** — never in source, never in commits, never in logs.
- **All outbound HTTP** has a timeout. All inbound HTTP has a rate limit (or a documented reason it does not).
- **All database access** uses parameterized queries. String-concatenated SQL is a bug, even if "this input is internal."

## OWASP top-10 quick checks

- Injection (SQL / command / LDAP) — parameterize.
- Broken auth — never roll your own; use the framework's auth primitives.
- Sensitive data exposure — TLS in transit, encrypted at rest for PII; redact secrets from logs.
- XML/JSON entity attacks — disable external-entity expansion in parsers.
- Broken access control — every endpoint enforces auth + ownership; default deny.
- Security misconfig — production env hardened (debug off, error pages don't leak stack traces).
- XSS — escape on output; use the framework's escaping primitives.
- Insecure deserialization — never deserialize attacker-controlled data into typed objects.
- Vulnerable components — keep deps current; track CVEs for what you ship.
- Insufficient logging — log auth failures and access-control denials, not just successes.

## Before merging

- No secrets in the diff (visually scan; consider a hook like `gitleaks`).
- New endpoints have an auth check.
- New input fields have a length limit and a validation step.
- New external HTTP has a timeout.

## Reporting a finding

If you discover a vulnerability mid-task, surface it immediately to the user — even if it's outside the current scope. Do not silently patch it without telling them.
