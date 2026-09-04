---
description: "Logic lives in exactly one service. Cross-boundary work must go through a documented interface."
globs: "*"
alwaysApply: true
---

# Rule 2: Service Boundaries

**When this applies:** any team with 2+ services in `voice-brain, mcp-tools, fabric, objective-engine`. Skip for single-service projects.

Each piece of business logic lives in exactly one service. If you find yourself reimplementing the same rule in two places, you have a boundary problem — extract a shared library or move the rule.

## Rules

- A service may **call** another service through a documented interface (REST, gRPC, message bus). It may not **reach into** another service's internals (database tables, file paths, private modules).
- Cross-cutting types live in a shared package. Other services depend on the package, not on each other.
- Database access is owned by exactly one service. Other services request data through that service's API.
- A "service" here means any of: `voice-brain, mcp-tools, fabric, objective-engine`. Frontends, backends, edge functions, jobs, and CLIs each count.

## Anti-patterns

- Frontend reading the database directly (bypasses backend validation, RLS, business rules).
- Two services with their own copies of the same enum/type, drifting independently.
- Service A importing service B's internal modules in tests.

## When you must cross a boundary

1. Document the new interface in the service's API contract.
2. Bump the contract's version.
3. Coordinate the rollout with `Rule 3: Cross-service sequence`.
