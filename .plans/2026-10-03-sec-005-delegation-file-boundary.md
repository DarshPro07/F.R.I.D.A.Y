# Plan: SEC-005 - a Hermes delegation's filesystem boundary, held below Hermes

**Date:** 2026-10-03
**Status:** In Progress (non-interactive build run; the owner's ruling of 2026-10-03 is the approval)
**Services:** objective-engine (hermes_bridge, delegation_scope), mcp-tools (execution_bridge, hermes_control)

## Objective
Owner ruling: advisory-only file roots are not accepted under HOST. A delegation's
only file access is FRIDAY's jail-backed file capability, scoped to the
delegation's envelope, enforced below Hermes. Hermes' internals are not touched.

## Design
- `friday/delegation_scope.py` (new, one owner of the logic): a registry of the
  envelopes of the delegations that are running, one file per governor lease,
  written by `HermesBridge.delegate` and read by the exec bridge on EVERY call
  (the bridge is spawned once per profile with a fixed env and no session id, so
  a session-scoped root cannot ride in its environment, and a model-supplied
  root would be a request, not a boundary).
- `resolve(raw, write=)`: relative paths start at the delegation's workspace, then
  `files.resolve_for` (fsjail: canonicalize, junction/symlink/reparse targets, UNC,
  denylist; then the envelope's read or write roots). Refused otherwise and logged.
  With several live delegations the bridge allows only what ALL of them allow, and
  a second delegation under a different workspace is refused at registration.
  An unreadable registry fails closed.
- `execution_bridge`: read/write/list/search go through `resolve`; with no live
  scope the profile workspace behaves exactly as before.
- `hermes_bridge`: `delegate(scope=envelope)` registers; the lease release at the
  terminal hook releases; an envelope-bound delegation is refused when the profile
  config visibly enables Hermes' native `file`/`terminal` toolsets.
- `fsjail._reject_shape`: `//host/share` (UNC with forward slashes) refused before
  the filesystem is touched.

## Commands: disabled, not advisory (owner ruling 2026-10-03c)
`bridge_run_command` runs a program, and a program (python, node, ...) opens any
path its user can, so file roots cannot confine it and an advisory shell is the
state the ruling rejects. While any delegation holds the bridge,
`delegation_scope.refuse_commands()` refuses every command first - before it is
parsed, with no executable allowlist - and the refusal names no program. The
`commands` part of `bounded_by` is reported `DISABLED` (`contracts.DISABLED`).
Unscoped calls are unchanged. Commands return only through EXEC-SBX-001
(AppContainer/LPAC or Windows Sandbox, after real escape tests).

## DOD
- [x] tests/test_delegation_scope.py: each case fails without the change
      (guard neutralised: the python write through the bridge returned exit=0)
- [x] existing envelope tests updated where they asserted the old ADVISORY truth
- [x] a scoped delegation's python/node/git/powershell command is refused and the
      outside file is not created, in-process and through a real stdio bridge
- [x] targeted suites green
