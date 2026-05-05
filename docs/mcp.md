# OpenVDI MCP Server

The `openvdi-admin` Model Context Protocol (MCP) server lets AI agents
operate an OpenVDI broker. It's the GTM layer: the broker stays free
and open source; paid agents drive it.

This document describes the MCP at a conceptual level. For installation
and operational mechanics, see `docs/deploy.md` → *MCP Server
Deployment*. For developer-level conventions, see
`mcp/openvdi-admin/README.md`.

## Contents

1. [Purpose and Scope](#purpose-and-scope)
2. [Architecture](#architecture)
3. [Service Account Setup](#service-account-setup)
4. [Environment Variables](#environment-variables)
5. [Tool Catalog](#tool-catalog)
6. [Read-Only Mode and the Confirm Pattern](#read-only-mode-and-the-confirm-pattern)
7. [Audit Trail Attribution](#audit-trail-attribution)
8. [Example Agent Configurations](#example-agent-configurations)
9. [Troubleshooting](#troubleshooting)
10. [Roadmap](#roadmap)

---

## Purpose and Scope

OpenVDI's MCP exposes the broker's admin API to AI agents through a
typed tool surface. Agents call tools (`openvdi_create_pool`,
`openvdi_diagnose_user`, etc.); the MCP authenticates against the
broker, makes the right HTTP calls, unwraps response envelopes, and
returns structured data the agent can reason about.

**What the MCP IS:**

- An operational management surface for AI agents driving OpenVDI.
- Stateless and pip-installable from the OpenVDI monorepo.
- Authenticated as a service-account AD user (member of the broker's
  `OPENVDI_LDAP_ADMIN_GROUP`).
- Version-locked to the broker — the version that ships with broker
  tag `m5-complete` is `0.5.0`.

**What the MCP is NOT:**

- A user-facing application. Users connect to OpenVDI via the
  React portal; the MCP is for agents.
- A bootstrap tool for setting up a fresh OpenVDI deployment. That's
  the planned `openvdi-installer` MCP, which is M6+ work with a
  different threat model.
- A replacement for the broker's REST API. Direct integrations should
  call the broker; the MCP is the AI-agent-friendly wrapper.
- A shim for talking to Proxmox directly. The broker is the integration
  layer with hypervisors; the MCP must NOT bypass it.

**Two MCP audiences:**

| Audience | Use case |
|---|---|
| **Praxova IT Agent platform** | Hosts the openvdi-admin MCP as a tool server. Customers running Praxova IT Agent can manage their OpenVDI deployment by talking to the agent. |
| **Direct AI tools** (Claude Desktop, Claude Code) | An OpenVDI operator runs the MCP as a subprocess of their AI assistant. Useful for one-off diagnosis, scenario testing, or scripting deployments. |

The MCP itself is Apache 2.0 and stays free. The agents that drive
it are where Praxova captures revenue.

---

## Architecture

```
┌──────────────────┐
│   AI Agent       │
│  (Claude /       │
│   IT Agent)      │
└────────┬─────────┘
         │ MCP protocol (stdio, JSON-RPC over framed messages)
         │ — tool calls in, results out
         ▼
┌─────────────────────────────────┐
│  openvdi-admin MCP server       │
│  (Python, FastMCP via mcp[cli]) │
│                                 │
│  Tools (43 total):              │
│  ├─ 37 thin wrappers            │
│  │  (one per admin endpoint)    │
│  └─ 6 intent tools              │
│     (compose thin wrappers)     │
│                                 │
│  BrokerClient                   │
│  ├─ JWT auth (service account)  │
│  ├─ Refresh-cookie handling     │
│  ├─ Envelope unwrapping         │
│  └─ X-Request-ID propagation    │
└────────────────┬────────────────┘
                 │ HTTPS (REST API + JWT bearer)
                 ▼
┌─────────────────────────────────┐
│   OpenVDI Broker (FastAPI)      │
│   - Admin endpoints             │
│   - Audit log (broker-side)     │
└─────────────────────────────────┘
```

The MCP is a sibling to the React portal — both consume the broker's
REST API; neither talks to the hypervisor or to Proxmox. The
architectural promise: changing hypervisor providers (vSphere,
Hyper-V) requires no MCP changes because the broker abstracts it.

**Layering invariants:**

- The MCP MUST NOT call provider APIs directly. The broker is the
  integration layer.
- Intent tools MUST NOT call broker REST endpoints directly — they go
  through thin wrappers. Refactoring is forced by structure.
- The MCP holds no persistent state. Restarts are safe at any time;
  state lives in the broker.

---

## Service Account Setup

The MCP authenticates as a regular AD user that's a member of the
group named in the broker's `OPENVDI_LDAP_ADMIN_GROUP` env var.

**Recommended naming:** `openvdi-mcp-svc` (one shared service
account per agent product is fine; if you want per-product audit
attribution, create separate accounts: `openvdi-itagent-svc`,
`openvdi-installer-svc`, etc.).

**Steps:**

1. Create the AD user via your usual provisioning (PowerShell, AD
   Users and Computers, your IDM tool, etc.).
2. Set a strong password. Loaded by the MCP as `SecretStr` and never
   logged.
3. Add the user to the broker's admin group (whatever
   `OPENVDI_LDAP_ADMIN_GROUP` points at).
4. Configure the MCP env vars per `docs/deploy.md` → *MCP Server
   Deployment*.

**Rotation policy:** rotate the password per your AD policy. Restart
the MCP after rotation — there's no SIGHUP-driven config reload in v0.

**Audit attribution:** every action the MCP triggers shows up in the
broker's `audit_log` with this username as `actor`. If the operator
asks "what did Bob do?", the answer requires looking at the agent
product's own logs alongside the broker audit, correlated via
`X-Request-ID`. The MCP can't distinguish "Bob via the agent" from
"the agent on its own" because both invocations use the same JWT.

**Why not pass-through user JWTs?** Future enhancement (M6+). v0
keeps the auth model simple. Pass-through would let the agent forward
the human operator's JWT, preserving native audit attribution. It
adds a config dimension and a refresh-failure UX worth designing
separately.

---

## Environment Variables

For the canonical table, see `docs/deploy.md` → *MCP Server
Deployment* → *Environment variables*. Highlights:

- `OPENVDI_BROKER_URL`, `OPENVDI_SERVICE_USER`, `OPENVDI_SERVICE_PASSWORD`
  — required at startup. MCP fast-fails if missing.
- `OPENVDI_MCP_READ_ONLY=true` — disables every destructive tool.
  Useful for diagnostic-only deployments.
- `OPENVDI_MCP_LOG_FORMAT=json` — recommended for production; mirrors
  the broker's M4-12 logging shape so cross-system grep works.
- `OPENVDI_MCP_LOG_TOOL_STARTS=true` — opt-in 2x log volume; useful
  when debugging hangs.

---

## Tool Catalog

The MCP exposes 43 tools, organized in two layers.

### Thin wrappers (37 tools)

One tool per admin endpoint. Naming convention: `openvdi_<verb>_<resource>`.

#### Clusters

| Tool | Purpose | Destructive |
|---|---|---|
| `openvdi_list_clusters` | List registered hypervisor clusters | No |
| `openvdi_get_cluster` | Get cluster details + live node status | No |
| `openvdi_create_cluster` | Register a new cluster | Yes |
| `openvdi_update_cluster` | Update cluster credentials or settings | Yes |
| `openvdi_delete_cluster` | Remove a cluster (rejects if pools depend on it) | Yes |

#### Templates

| Tool | Purpose | Destructive |
|---|---|---|
| `openvdi_list_templates` | List VDI templates | No |
| `openvdi_get_template` | Get template details | No |
| `openvdi_register_template` | Register a Proxmox template VM as a VDI template | Yes |
| `openvdi_update_template` | Update template metadata | Yes |
| `openvdi_validate_template` | Re-check template against Proxmox | Yes |
| `openvdi_retire_template` | Retire (destroys; rejects if pools depend on it) | Yes |

#### Entitlements

| Tool | Purpose | Destructive |
|---|---|---|
| `openvdi_list_entitlements` | List a pool's entitlements | No |
| `openvdi_grant_entitlement` | Grant pool access to a user or group | Yes |
| `openvdi_revoke_entitlement` | Revoke an entitlement | Yes |

#### Pools

| Tool | Purpose | Destructive |
|---|---|---|
| `openvdi_list_pools` | List pools | No |
| `openvdi_get_pool` | Get full pool detail with capacity counts | No |
| `openvdi_get_pool_summary` | Compact health summary (synthesized from get_pool) | No |
| `openvdi_create_pool` | Create a pool | Yes |
| `openvdi_update_pool` | Update pool settings (no status changes — use drain or delete) | Yes |
| `openvdi_delete_pool` | Delete a pool, cascading desktops + entitlements | Yes |
| `openvdi_provision_pool` | Provision N desktops (polls until terminal state) | Yes |
| `openvdi_drain_pool` | Drain a pool (one-way: status → 'draining'; delete to remove) | Yes |

#### Dashboard

| Tool | Purpose | Destructive |
|---|---|---|
| `openvdi_get_dashboard_summary` | Aggregate deployment stats | No |
| `openvdi_get_dashboard_capacity` | Per-pool capacity breakdown | No |

#### Audit

| Tool | Purpose | Destructive |
|---|---|---|
| `openvdi_query_audit` | Query the broker audit log | No |

#### Desktops

| Tool | Purpose | Destructive |
|---|---|---|
| `openvdi_list_desktops` | List desktops (filterable) | No |
| `openvdi_get_desktop` | Get desktop detail | No |
| `openvdi_assign_desktop` | Assign a desktop to a user | Yes |
| `openvdi_unassign_desktop` | Clear an assignment | Yes |
| `openvdi_rebuild_desktop` | Destroy + re-clone (preserves assignment) | Yes |
| `openvdi_power_desktop` | Power control (action: start/stop/shutdown/reboot) | Yes |
| `openvdi_delete_desktop` | Destroy a desktop | Yes |

#### Sessions

| Tool | Purpose | Destructive |
|---|---|---|
| `openvdi_list_sessions` | List sessions | No |
| `openvdi_get_session` | Get session detail (includes guest agent telemetry) | No |
| `openvdi_force_disconnect_session` | End an active session | Yes |

#### User Diagnostics

| Tool | Purpose | Destructive |
|---|---|---|
| `openvdi_list_user_desktops` | List a user's directly-entitled pools | No |
| `openvdi_list_user_sessions` | List a user's sessions | No |

### Intent tools (6 tools)

Compose thin wrappers into high-value workflows. Each returns a
structured `IntentResult` envelope: `{ok, operation, result | error_*,
steps[], failed_at_step?}`.

#### `openvdi_smoke_test`

Verifies that a pool can produce an operational desktop. Stops short
of issuing a console ticket — that requires user-scoped JWTs the MCP
can't impersonate. Useful as the agent's "is this pool actually
working?" question.

Validates: broker reachable, cluster active, pool config correct,
VMID allocator works, Proxmox clone works, guest agent boots, status
reporting works.

#### `openvdi_deploy_pool`

Stand up a complete pool: validate template, create pool, grant
entitlements, optionally pre-provision warm spares. Returns a
structured envelope on success or partial failure (with a
`rollback_hint` pointing at the resources that need cleanup).

Use case: an operator (or agent) deploying a new desktop pool from
existing template/cluster registrations. Composes 4-7 thin wrapper
calls into one.

#### `openvdi_reset_test_environment`

Nuclear cleanup of test pools. Drops every pool whose name starts
with a configurable prefix (default `test-`), force-disconnects
active sessions, deletes the pool. Optionally cascades to templates
and clusters when they have no remaining pool references.

Hard-coded safety: refuses empty or wildcard prefixes. `confirm=False`
returns a dry-run preview.

Use case: regression testing, beta scenario cleanup, scripted
test-pool lifecycles.

#### `openvdi_diagnose_user`

"Why can't Alice connect?" — single-tool answer. Combines Alice's
direct entitlements, current assignments, sessions, and per-pool
blocking factors (POOL_FULL, POOL_DRAINING, etc.).

The honest caveat: the MCP can't query AD group memberships. Pools
Alice can access via group membership are surfaced as
`potential_group_entitlements` for the agent (or its IT Agent
companion) to verify with AD tooling. The MCP tells you what it
knows; it won't pretend to know what it doesn't.

#### `openvdi_diagnose_pool`

Pool health snapshot: capacity, error desktops with their
`error_message`, stuck-provisioning detection, recent audit events,
cluster status. Rolls up to a single `health` field
(`healthy`/`degraded`/`unhealthy`) and an `issues` list with
`severity` and `suggested_action` per issue.

Use case: triage. "What's wrong with the engineering pool?" The
diagnose tool gathers what the agent would otherwise need 5-6
separate calls for.

#### `openvdi_health_check`

Lightweight session-start tool. Pings broker `/health`, lists
clusters with their statuses, returns a one-line summary. Agents
should call this at session start to verify connectivity before
proceeding.

---

## Read-Only Mode and the Confirm Pattern

Two layers of safety, complementary.

### Read-only mode (operator-side switch)

Set `OPENVDI_MCP_READ_ONLY=true` and every destructive tool refuses
to execute, returning a structured `READ_ONLY_MODE` error to the
agent. Use cases:

- **Diagnostic-only deployments.** An IT Agent pod that should
  investigate but never mutate.
- **Production safety net.** A deployment where mutations should
  only happen via a separate ticket-tracked process.
- **Onboarding.** Limit a new agent integration to read access until
  trust is established.

What stays available: every `list_*`, `get_*`, dashboard tool, audit
query, user diagnostics tool, and all six intent tools. Diagnosis
works fully even in read-only mode.

What's disabled: every `create`, `update`, `delete`, `power`,
`provision`, `drain`, `rebuild`, `assign`, `unassign`,
`force_disconnect`, `grant`, `revoke`, plus the destructive intent
tools (deploy_pool, reset_test_environment).

### Confirm pattern (per-call deliberation)

Every destructive thin-wrapper tool takes `confirm: bool = False`.
Calling without `confirm=True` returns a dry-run preview describing
what *would* happen — current state, affected dependencies (pools
that would be cascaded, sessions that would end), and a `note` field
explaining the consequences.

The agent must then either pass `confirm=True` or back off. The
human supervisor sees the `confirm=True` parameter in the tool call
audit, making the decision visible.

Example:

```
Agent: openvdi_delete_pool(pool_id="abc")
       ↓
       Tool returns:
       {
         "dry_run": true,
         "action": "delete_pool",
         "target": {"id": "abc", "name": "engineering"},
         "extra": {
           "would_destroy": {
             "desktops": 10,
             "active_sessions": 4,
             "entitlements": 2
           }
         },
         "note": "Cascades through desktops..."
       }
       ↓
       Agent: openvdi_delete_pool(pool_id="abc", confirm=True)
       ↓
       Tool executes.
```

The dry-run is honest about what the broker would actually do; it
queries dependent resources at preview time. Between dry-run and
confirm, state may change — the broker validates again at execute
time and returns CONFLICT if dependencies have shifted.

Intent tools have a similar pattern but with their own envelope
shape (see `openvdi_deploy_pool`, `openvdi_reset_test_environment`).

---

## Audit Trail Attribution

The MCP itself does NOT write its own audit log. The broker's
`audit_log` table (per M2-12) is the audit trail. Every MCP-driven
action shows up there with:

- `actor` — the MCP's service-account username.
- `action` — the broker's audit action code (`broker.connect`,
  `admin.cluster.create`, etc.).
- `resource_type` and `resource_id` — what was acted on.
- `details` — JSONB of the action; sensitive fields redacted per
  M2-12.
- `request_id` (in details) — the MCP's `X-Request-ID` UUID.

To trace "what did Claude Desktop do at 14:22?":

1. Find the request_id in the MCP's stderr log (with
   `OPENVDI_MCP_LOG_FORMAT=json`):
   ```
   $ jq 'select(.timestamp > "2026-04-30T14:22")' mcp.log
   ```
2. Grep the broker structured log for the same UUID:
   ```
   $ grep <request_id> /var/log/openvdi/broker.log
   ```
3. Query the audit log:
   ```sql
   SELECT * FROM audit_log
   WHERE details->>'request_id' = '<rid>'
   ORDER BY timestamp;
   ```

Tool args are NOT logged on the MCP side. They appear in the broker's
audit_log with sensitive fields redacted. This is deliberate: the MCP
log is operational telemetry; audit lives in the broker.

For per-agent attribution (distinguishing IT Agent from a customer
installer), use separate AD service accounts per agent product. The
audit_log's `actor` field reflects which one acted.

---

## Example Agent Configurations

For Claude Desktop and Claude Code config snippets, see
`mcp/openvdi-admin/examples/`. The examples are minimal — a snippet
to merge into your existing config, not a full file. The example
README explains the merge.

For the Praxova IT Agent platform integration: see the IT Agent
platform docs. The MCP integrates as a tool server using the same
env-var contract documented in `docs/deploy.md`.

---

## Troubleshooting

Real gotchas surfaced during M5 implementation.

### `pip install -e ".[dev]"` stalls

The `mcp[cli]` SDK has substantial transitive dependencies; on
constrained hosts the install can stall. Workaround:

```bash
pip install --no-deps -e .
pip install pytest pytest-asyncio respx ruff mypy
pip install "mcp[cli]>=1.0.0"   # let it complete in background
```

The `mcp[cli]` install must eventually complete — it's not optional.
Tests that exercise FastMCP machinery (`mcp.list_tools()`) need it.

### "Tool returned READ_ONLY_MODE; I'm sure I unset that env var"

Settings are loaded once at MCP startup via `pydantic-settings` with
`@lru_cache`. Changing the env var in the calling shell does NOT
propagate to the MCP. Restart the MCP. (Per A6 in the design seed —
no SIGHUP-driven config reload in v0.)

### `openvdi_drain_pool` hangs / never returns

Drain is one-way. The broker transitions a pool from `active` to
`draining` and stops there — no automatic flip back to `disabled`
once sessions reach zero. The MCP's polling waits for active session
count to reach 0; if it does, the tool returns. If you want the pool
gone, follow drain with `openvdi_delete_pool(pool_id, confirm=True)`.

### "Provision returned but my pool only has 4 desktops, not 5"

Check `pool["capacity"]["error"]` in the result — failed clones land
in error state and don't count toward `available`. The broker's
`pool_provisioner` worker logs the actual Proxmox error
(`/var/log/openvdi/broker.log` filtered by `worker=pool_provisioner`).
Common causes: out-of-VMID-range, storage full, template lock
contention.

### "Tool got `READ_ONLY_MODE` for a power-cycle"

Power actions (start, stop, shutdown, reboot) are destructive in the
read-only-mode sense — they all change broker state. Read-only mode
permits only list/get/dashboard/audit/diagnose tools. If the use
case is "let agents observe but not power-cycle," that's the v0
behavior; granular tiers ("read + power, no delete") are M6+ per
the IT Agent platform's policy layer.

### Different `capacity` shapes in different tool outputs

`openvdi_get_pool` returns `pool["capacity"]["total_desktops"]`
(broker's native `PoolCapacityDetail` schema field name).
`openvdi_get_pool_summary` returns `summary["capacity"]["total"]`
(synthesized for agent ergonomics). The two tools have intentionally
different shapes:

- `get_pool` — full broker state, exact field names. Use when you
  need other broker fields.
- `get_pool_summary` — flatter shape, includes a precomputed
  `issues` array. Use for "is this pool healthy?" questions.

If you mix them and access `capacity.total` on a `get_pool` result,
you get None or KeyError depending on access style.

### "MCP can't see Alice's group memberships"

Right. The MCP doesn't query LDAP — only the broker does (during
login). The admin endpoints the MCP consumes (`/admin/users/{username}/desktops`)
return DIRECT entitlements only. Pools Alice can access via group
membership surface as `potential_group_entitlements` for the agent
to verify. If the agent has access to AD tooling (e.g. via the IT
Agent platform's other tool servers), it resolves group membership
there and merges the answer.

### Tool returns `result_envelope_ok: false` but the log says outcome=ok

The `outcome` field reflects whether the tool's Python function
completed without raising. `result_envelope_ok` is only set for
intent tools that return an `IntentResult`-shaped dict — it
distinguishes "intent tool returned a structured success" from
"intent tool returned a structured failure" (e.g.
`openvdi_diagnose_user` for a username with no entitlements at all).

The combination `outcome=ok, result_envelope_ok=false` is normal —
the tool ran successfully, but the operation it represented didn't
succeed (e.g. no desktops to verify in `openvdi_smoke_test`). The
agent reads the result envelope to interpret.

### Long-running tool calls block the agent UI

Some operations (rebuild, drain, deploy_pool) genuinely take
30s-10min. The MCP polls until terminal-state or timeout, returning
the final state in one synchronous-looking call. Per T6.

This is by design — agents prefer one return over polling-loop logic
in their own code. If the timeout fires before completion, the tool
returns the LAST observed state (with `provisioning` or `draining`
status still set); the agent can re-query via `openvdi_get_pool` if
desired.

For now: long-running calls block. Streaming progress is M6+ work
when the MCP spec / FastMCP SDK supports it cleanly.

### "I want to see what args the agent passed"

The MCP doesn't log tool args (sensitive data risk; redaction is
brittle). Args are visible in the broker's `audit_log` with
sensitive fields redacted per M2-12. Correlate via `request_id`:

```sql
SELECT actor, action, resource_type, resource_id, details
FROM audit_log
WHERE details->>'request_id' = '<rid>';
```

### Cross-MCP correlation (IT Agent + openvdi-admin + pve-spec-query)

Each MCP generates its own `request_id`; there's no shared
correlation header in v0. Agents wanting cross-MCP traces would
need their own correlation-ID-propagation mechanism. The IT Agent
platform may add this in its policy layer.

---

## Roadmap

Items deferred from v0 (see seed's *What M5 does NOT ship*):

- **`openvdi-installer` MCP.** Bootstrap-from-bare-Proxmox onboarding.
  Different threat model and auth story; needs its own design pass.
- **Pass-through user JWTs.** The MCP forwards the operator's JWT
  instead of using a service account. Adds a config dimension and a
  refresh-failure UX.
- **Streaming progress.** Long-running tools surface step-completion
  events as the agent waits. Lands when MCP transport supports it
  cleanly.
- **MCP-side audit log.** Every tool call gets a separate audit record
  in the MCP itself, distinguishable from broker audit. v0 relies on
  broker audit + request_id correlation.
- **Rate limiting.** No throttling against agent misbehavior in v0.
- **MCP packaging as Docker image.** v0 is pip-installable from the
  monorepo.
- **Multi-cluster orchestration.** Intent tools take one
  cluster/pool at a time; cross-cluster ops are M6+.
- **Granular capability tiers.** Beyond binary read-only mode. The
  IT Agent platform's policy layer is the natural home for this; the
  MCP itself stays binary.
- **Pre-canned MCP prompts / agent personas.** Anthropic's MCP spec
  allows servers to ship prompts as well as tools. v0 ships tools
  only.

When real beta usage surfaces specific demand for any of these, they
move up.
