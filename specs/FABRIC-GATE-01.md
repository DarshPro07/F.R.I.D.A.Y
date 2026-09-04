# FABRIC-GATE-01 — Permissions and secrets enforced at `fabric.call()`

**Closes:** G7 · **Status:** specified, not built · **Depends on:** nothing
**Blocks:** shipping `FABRIC-CLI-01` or `FABRIC-SVC-01` in anger

## Why

`Provider.permissions` and `Provider.secrets` are declared fields that nothing
reads on the direct path. `fabric.call()` resolves the provider, checks the
operation is declared, activates, and invokes. It never consults
`provider.permissions` and never resolves `provider.secrets`.

The gate exists only in `candidates()`/`select()`, reached via
`call_with_fallback(authorized=...)`. So authorisation is enforced on one entry
point and absent on the other, and `fabric.call(provider_id, operation)` —
a public function — bypasses it entirely.

Today the blast radius is small: five providers execute anything, three are
trusted Python libraries. `FABRIC-CLI-01` and `FABRIC-SVC-01` change that by
design. This must land first or with them, not after.

## Change

Move the check into `call()`, which is the single choke point every path
already funnels through — `call_with_fallback()` calls `call()`.

```python
def call(provider_id, operation, *, run_id="",
         authorized: frozenset[str] | None = None, **arguments):
    provider = get(provider_id)
    ...
    missing = set(provider.permissions) - (authorized or frozenset())
    if missing:
        return c.failed(result,
            f"{provider_id} needs {sorted(missing)}; not granted")
```

`authorized=None` means "no grants", not "all grants". Failing closed is the
same rule `capabilities.requires_approval` already applies to unknown tool ids,
and for the same reason: unaudited must not mean allowed.

`call_with_fallback()` stops filtering candidates by permission and simply
threads `authorized` down. One implementation, one place to get right — the
current arrangement has the rule written twice and enforced once.

## Secrets

`call()` resolves `provider.secrets` through the secret broker and passes the
values to the adapter as a separate `secrets` mapping, keyed by name:

```python
value = invoke(operation, activation.handle, secrets=resolved, **arguments)
```

Adapters stop reading `os.environ`. This matters because `FABRIC-PROC-01`
scrubs the child environment: an adapter that reaches for `os.environ` today
would silently get nothing tomorrow, and "silently gets nothing" is how a
credential bug becomes a mystery.

A missing required secret is `AUTH_REQUIRED` with the secret **name** in the
detail, never a value, and never a partial value.

## Redaction

Resolved secret values are registered with the log redactor for the duration
of the call, so a third-party adapter that helpfully echoes its configuration
cannot leak the key into `logs/fabric/*.log`.

## Acceptance

Tests in `tests/test_fabric_gate.py`:

1. `fabric.call()` on a provider declaring a permission, with no `authorized`,
   returns `failed` naming the permission. It does **not** activate the
   provider — the check precedes activation, so a gate failure cannot start a
   process as a side effect.
2. The same call with the permission granted succeeds.
3. `call_with_fallback` and `call` refuse identically for the same provider —
   no path is more permissive than the other.
4. A declared secret is resolved and reaches the adapter as `secrets[name]`.
5. A missing secret yields `AUTH_REQUIRED` with the name and not the value.
6. A secret value emitted by an adapter to the log is redacted.
7. `authorized=None` is treated as no grants, not all grants (fail closed).

## Migration

Existing adapters take `**kwargs`, so adding `secrets=` is backward compatible
for any adapter that ignores it. The three `ADAPTER`-mode providers
(`graphiti_memory`, `mem0_memory`, `scrapling_parse`) declare no permissions
today, so they are unaffected. `codebase_memory` declares an MCP process and no
permissions; it must be reviewed and given `filesystem.read` rather than left
implicitly unrestricted — the review is part of this spec's work, not a
follow-up.
