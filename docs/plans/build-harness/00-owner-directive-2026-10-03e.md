# Owner ruling 2026-10-03 (e): MCP-001 - Hermes discovery, approval waiver, protocol pinning

The owner's answer, extracted verbatim from the session transcript, to the three questions MCP-001's second PARTIAL integration left (2026-10-03, wave 68): where Hermes' MCP config lives; who may waive approval of an external server and when; whether to pin the registered or the negotiated protocol version.

---

Here are my rulings for `MCP-001`.

**1. Hermes config: update discovery to read Hermes’ real live config, not just `mcpServers` JSON.** Hermes’ runtime source is `$HERMES_HOME/config.yaml` under the YAML key `mcp_servers`. For the default profile that is `~/.hermes/config.yaml`; named profiles live under `~/.hermes/profiles/<name>/config.yaml`. Hermes can import Claude Code’s JSON `mcpServers` and Codex MCP config, but it converts those into Hermes’ YAML representation. [GitHub](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/mcp.md?utm_source=chatgpt.com)

So implement discovery roughly as:

```text
FRIDAY managed registry
        +
Hermes live config:
    $HERMES_HOME/config.yaml
    key = mcp_servers
        +
optional external discovery:
    Claude Code JSON
    Codex config
        ↓
EXTERNAL_DISCOVERED
```

Do **not** treat Hermes profile-distribution `mcp.json` as the normal live source of truth; distributions may ship it, but normal runtime MCP configuration is in `config.yaml`. [GitHub](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/profile-distributions.md?utm_source=chatgpt.com)

For Windows, `$HERMES_HOME` normally resolves under the user profile, e.g. `C:\Users\<user>\.hermes\...`.

---

**2. Approval waiver: first activation of an external server should require owner/admin approval; subsequent use can be automatic if its verified identity and authority have not changed.**

Use:

```text
MANAGED_BY_FRIDAY + previously approved + fingerprint unchanged
→ no repeated approval

EXTERNAL_DISCOVERED first activation
→ approval required

EXTERNAL_DISCOVERED already approved
+ exact trust contract still valid
→ no repeated approval

anything materially changed
→ SUSPEND + reapproval/revalidation
```

A waiver can come from only:

```text
A. explicit user/owner approval
B. workspace administrator policy
C. an existing standing approval lease that explicitly covers
   this exact MCP server/trust class
```

The model itself **cannot waive approval**.

I would allow silent reconnect/restart only when all of these remain unchanged:

```text
server identity
transport class
origin / executable identity
permission/risk class
credential scope
tool authority ceiling
approved workspace
trust-policy version
```

Changes in ordinary non-authority metadata do not need to nag the user.

Hermes itself follows a similar conservative pattern for adding MCP connections: its one-click install flow displays the configuration and requires explicit confirmation rather than silently installing it. [GitHub](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/mcp-config-reference.md?utm_source=chatgpt.com)

So the useful lifecycle is:

```text
EXTERNAL_DISCOVERED
→ EXTERNAL_UNVERIFIED
→ VERIFIED_PENDING_APPROVAL
→ ACTIVE
```

Later:

```text
safe reconnect
→ ACTIVE automatically

authority/identity change
→ SUSPENDED
→ REVALIDATE
→ approval if necessary
```

That gives FRIDAY autonomy without approval spam.

---

**3. Protocol pinning: do NOT pin the exact negotiated version as immutable server identity. Store both an allowed protocol policy and the observed runtime version.**

This is important because MCP version negotiation is designed to choose a version that both client and server support. In handshake-era MCP, the client can request one revision and the server can return another supported revision; newer SDKs deliberately negotiate this automatically. [Java SDK Model Context Protocol](https://java.sdk.modelcontextprotocol.io/latest-snapshot/client/?utm_source=chatgpt.com)

Also, the July 2026 MCP revision changed lifecycle substantially: there is no longer a normal initialization handshake/session; the protocol version is carried per request. So “pin whatever was negotiated last time forever” becomes the wrong abstraction. [Model Context Protocol Blog](https://blog.modelcontextprotocol.io/posts/2026-07-28/?utm_source=chatgpt.com)

Store:

```yaml
protocol:
  policy:
    allowed_eras:
      - legacy
      - modern

    allowed_versions:
      - "2025-11-25"
      - "2026-07-28"

  registered_preference:
    "auto"

  last_observed_version:
    "2026-07-28"

  last_observed_era:
    modern
```

Then:

```text
new negotiated/adopted version
        ↓
within approved compatibility policy?
        │
        ├─ YES
        │    ↓
        │  record new observed version
        │  rerun semantic health/schema checks
        │  DON'T suspend merely for version number
        │
        └─ NO
             ↓
          SUSPEND
          revalidate
```

I would trigger suspension on things that actually change the trust contract:

```text
legacy ↔ modern lifecycle change if not already approved
unsupported protocol version
server identity changed
endpoint changed
local executable/hash changed
tool set gains higher authority
tool schema materially changed
credential/auth scope expands
security-sensitive capabilities change
```

Not:

```text
MCP Python SDK upgraded
→ negotiated 2025-11-25 instead of 2025-06-18
→ everything suddenly dead
```

That would be brittle.

### So tell the worker

```text
MCP-001 RULINGS

1. Hermes:
   Discover live MCP servers from
   $HERMES_HOME/config.yaml -> mcp_servers.
   Default HERMES_HOME is ~/.hermes.
   Named profiles use ~/.hermes/profiles/<name>.
   Keep JSON mcpServers parsing only for external/import discovery,
   not as Hermes' canonical source.

2. Approval:
   First activation of EXTERNAL_DISCOVERED MCP requires explicit
   owner/admin approval unless an existing standing approval lease
   explicitly covers that exact server/trust class.

   Once approved and verified, reconnects/restarts do not prompt again
   while identity, authority, credential scope and trust contract remain
   unchanged.

   Models may never waive approval themselves.

3. Protocol:
   Do not hard-pin last negotiated protocol version as identity.

   Persist:
     registered/preferred policy,
     allowed protocol versions/eras,
     observed negotiated/adopted version.

   Compatible negotiated-version movement is telemetry + revalidation,
   not automatic suspension.

   Suspend only on version outside policy, unapproved lifecycle/era
   transition, identity change, authority/schema expansion or other
   trust-contract change.
```

That should let `MCP-001` move forward without making normal MCP upgrades a maintenance nightmare.
