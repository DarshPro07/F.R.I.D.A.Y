"""The fabric's vocabulary - licence modes, integration modes and provider states - with no
call surface. A toolset may need to speak these words without reaching the fabric's registry or a
provider (tests/test_envelope_workers.py fails any toolset that imports friday.fabric: a call from a
toolset must hold run.envelope first, SEC-005). friday.fabric re-exports every name here, so
`fabric.READY` and `from friday.fabric import READY` keep working and there is one spelling."""

#: How the upstream's license constrains the integration, not the license name.
#:
#: The distinction that matters at build time is "may this be imported into
#: Friday's own process" - everything else is a lawyer's problem, and this
#: field is the engineering half of it.
PERMISSIVE = "PERMISSIVE_IMPORT"      # MIT / BSD / Apache-2.0
COPYLEFT = "ISOLATED_SIDECAR"         # AGPL-3.0 / GPL-3.0
SEPARATE = "SEPARATE_LICENSE"         # e.g. OpenHands enterprise/
BUILTIN_LICENSE = "NONE"              # ours; no upstream involved
LICENSE_MODES = (PERMISSIVE, COPYLEFT, SEPARATE, BUILTIN_LICENSE)

#: Integration modes that put a process or network boundary between Friday and
#: the upstream. Copyleft providers may only use these.
BUILTIN = "BUILTIN"
ADAPTER = "ADAPTER"                   # imported into Friday's process
MCP = "MCP"                           # separate process, MCP protocol
SKILL = "SKILL"                       # prompt/recipe only, no code executed
SIDECAR = "SIDECAR"                   # separate process/service
REFERENCE_ONLY = "REFERENCE_ONLY"     # read for patterns; never executed
#: One-shot subprocess: invoke, work, exit. The seven command-line agents in
#: third_party/upstream had no mode that described them, so they were
#: unreachable by construction rather than by omission.
CLI = "CLI"
INTEGRATION_MODES = (BUILTIN, ADAPTER, MCP, SKILL, SIDECAR, REFERENCE_ONLY, CLI)

#: The modes that do not link upstream code into Friday's address space.
#: CLI belongs here, and that is the point: a subprocess is a process boundary,
#: so a copyleft agent becomes integrable without touching the licence
#: invariant. The invariant was a wall with no door; this is the door.
ISOLATED_MODES = frozenset({MCP, SIDECAR, SKILL, REFERENCE_ONLY, CLI})

#: Runtime state. Distinct from "is it in the registry", which is always true
#: for anything this module can name.
REGISTERED = "REGISTERED"        # known, never activated this session
READY = "READY"                  # activated and its health probe agrees
DEGRADED = "DEGRADED"            # answering, but not fully - say so, use it
AUTH_REQUIRED = "AUTH_REQUIRED"  # a person must supply a credential
UNAVAILABLE = "UNAVAILABLE"      # tried, could not reach it
DISABLED = "DISABLED"            # deliberately switched off
FAILED = "FAILED"                # reachable, but its last call raised (FR-026)
STATES = (REGISTERED, READY, DEGRADED, AUTH_REQUIRED, UNAVAILABLE, DISABLED,
          FAILED, REFERENCE_ONLY)
