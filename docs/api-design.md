# OpenVDI API Design

## Overview

FastAPI REST API serving both the admin dashboard and user-facing web portal. JWT-based authentication with AD/LDAP backend. All endpoints under `/api/v1`.

**Primary consumers.** The admin endpoints documented below are consumed
by the React portal (M3/M4) and the `openvdi-admin` MCP server (M5).
Both authenticate via the same JWT mechanism described in the
*Authentication* section. The MCP wraps every admin endpoint as a
thin tool (`openvdi_<verb>_<resource>`); see `docs/mcp.md` for the
catalog. The `/me/*` user endpoints are consumed only by the portal —
the MCP uses the admin equivalents (e.g. `/admin/users/{username}/desktops`)
to operate on behalf of arbitrary users.

## Authentication

OpenVDI uses JWT-based authentication backed by LDAP/AD. Three endpoints under `/api/v1/auth` constitute the auth surface. All other endpoints require a valid access token in the `Authorization: Bearer <token>` header (per the M4 middleware).

### Tokens

- **Access token** — HS256-signed JWT, 15-minute TTL. Carries `sub` (canonical lowercase username), `groups[]`, `role`, `iat`, `exp`, and `jti` claims. Stateless; the broker validates the signature locally and trusts the claims for the token's lifetime.
- **Refresh token** — opaque `<id>.<secret>` payload stored in an HttpOnly + Secure + SameSite=Strict cookie at `/api/v1/auth`. 24-hour TTL. The id half is the `auth_tokens` row id; the secret half is bcrypt-verified against `auth_tokens.refresh_hash`.

The portal stores the access token in memory (React state) and lets the browser manage the refresh cookie. Same-origin deployment is required for SameSite=Strict to permit the cookie on `/auth/refresh` calls — see `docs/deploy.md` → *Same-Origin Requirement*.

### Endpoints

#### `POST /api/v1/auth/login`

Request body:
```json
{"username": "alice", "password": "..."}
```

Response (200):
```json
{
  "data": {
    "access_token": "eyJhbGc...",
    "expires_in": 900,
    "role": "user"
  },
  "error": null
}
```

`Set-Cookie: refresh_token=<id>.<secret>; HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth; Max-Age=86400`

Errors:
- `401 UNAUTHORIZED` — invalid credentials.
- `503 SERVICE_UNAVAILABLE` — LDAP unreachable.

#### `POST /api/v1/auth/refresh`

No request body. Reads the refresh cookie. On success returns a fresh access token + a rotated refresh cookie (same id, new secret). The refresh path also re-fetches the user's groups + admin status from LDAP, so privilege changes propagate within `expires_in` seconds of the next refresh.

Errors:
- `401 UNAUTHORIZED` — cookie missing, malformed, expired, revoked, or secret mismatch (a single response covers all five — distinguishing them would leak information).
- `503 SERVICE_UNAVAILABLE` — LDAP unreachable.

#### `POST /api/v1/auth/logout`

No request body. Revokes the refresh row (sets `revoked_at`) and clears the cookie. Always returns `204 No Content` — idempotent. Logout never returns 4xx; a missing or malformed cookie still produces 204.

### Token revocation posture

Access tokens are stateless — the 15-minute TTL is the security boundary. Logout revokes the refresh row; previously-issued access tokens remain valid until they expire. Emergency revocation of all access tokens requires rotating `OPENVDI_JWT_SECRET` and restarting all brokers.

### Dev-mode authentication (development only)

The M2 `X-Dev-User` / `X-Dev-Groups` / `X-Dev-Role` header path is preserved behind `OPENVDI_AUTH_MODE=dev`. In dev mode, the JWT endpoints under `/api/v1/auth` return `503 AUTH_MODE_NOT_SUPPORTED` — local development uses headers, not JWTs.

The broker's default is `OPENVDI_AUTH_MODE=jwt`; production deployments don't need to set anything (the M4-02 model validator enforces the required env-var set when in jwt mode). See `docs/deploy.md` → *Environment Variables* for the production env-var set.

| Header | Meaning |
|--------|---------|
| `X-Dev-User` | Username (e.g. `jsmith`) |
| `X-Dev-Groups` | Comma-separated AD group names (e.g. `Engineering,VPN Users`) |
| `X-Dev-Role` | `admin` or `user` |

## Admin Endpoints

Require `role=admin` in JWT.

### Clusters

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/clusters` | List Proxmox cluster connections |
| `POST` | `/clusters` | Register a new cluster |
| `GET` | `/clusters/{id}` | Get cluster details + live node status from Proxmox |
| `PUT` | `/clusters/{id}` | Update cluster config |
| `DELETE` | `/clusters/{id}` | Remove cluster (must have no pools) |

### Templates

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/templates` | List templates (filterable by cluster, os_type, status) |
| `POST` | `/templates` | Register a Proxmox VM template as VDI golden image |
| `GET` | `/templates/{id}` | Get template details |
| `PUT` | `/templates/{id}` | Update template metadata |
| `POST` | `/templates/{id}/validate` | Verify template exists in Proxmox, agent installed, snapshot OK |
| `DELETE` | `/templates/{id}` | Retire template (must have no active pools) |

### Pools

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/pools` | List all pools with summary stats |
| `POST` | `/pools` | Create a pool (validates VMID range, creates PVE pool if configured) |
| `GET` | `/pools/{id}` | Pool detail including desktop list and capacity stats |
| `PUT` | `/pools/{id}` | Update pool settings |
| `DELETE` | `/pools/{id}` | Delete pool (destroys all unassigned desktops, rejects if active sessions) |
| `POST` | `/pools/{id}/provision` | Trigger provisioning to min_spare (manual override) |
| `POST` | `/pools/{id}/drain` | Set status to draining, stop provisioning, wait for sessions to end |

### Pool Entitlements

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/pools/{id}/entitlements` | List entitlements for a pool |
| `POST` | `/pools/{id}/entitlements` | Grant pool access to user or AD group |
| `DELETE` | `/pools/{id}/entitlements/{ent_id}` | Revoke access |

### Desktops

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/desktops` | List all desktops (filterable by pool, status, assigned_user) |
| `GET` | `/desktops/{id}` | Desktop detail (includes current session, VM status) |
| `POST` | `/desktops/{id}/assign` | Manually assign desktop to a user |
| `POST` | `/desktops/{id}/unassign` | Remove user assignment |
| `POST` | `/desktops/{id}/rebuild` | Destroy VM, re-clone from template, preserve assignment |
| `POST` | `/desktops/{id}/power/{action}` | Power control: start, stop, shutdown, reboot |
| `DELETE` | `/desktops/{id}` | Destroy desktop VM and remove record |

### Sessions

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/sessions` | List sessions (filterable by pool, user, status, time range) |
| `GET` | `/sessions/{id}` | Session detail including guest agent telemetry |
| `DELETE` | `/sessions/{id}` | Force disconnect session |

### Audit

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/audit` | Query audit log (filterable by actor, action, resource, time range) |

### Dashboard

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/dashboard/summary` | Aggregate stats: total pools, desktops, active sessions, capacity |
| `GET` | `/dashboard/capacity` | Per-pool capacity breakdown (total, available, assigned, connected) |

### User Diagnostics (Admin)

Read-only endpoints for diagnosing a specific user's account state. Used by
the OpenVDI MCP server's `openvdi_diagnose_user` intent tool. Username
matching is case-insensitive (canonicalized to lowercase before lookup).

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/admin/users/{username}/desktops` | List pools the user is entitled to (DIRECT user entitlements only) with any current assignment |
| `GET` | `/admin/users/{username}/sessions` | List the user's sessions, newest-first; supports `?include_ended=true` and `?limit=N` |

**Note on group entitlements.** These endpoints surface pools entitled via
direct user match only. Pools accessible via group membership are NOT
included — the admin's JWT does not carry the target user's group
memberships, and the broker does not query AD from admin endpoints. The
MCP's diagnose tool resolves group entitlements through a separate query
against `/api/v1/pools/...?entitlement_principal_type=group`.

**Note on missing users.** If the username has no direct entitlements and
no sessions, the response is `{data: [], error: null}` with HTTP 200.
There is no canonical "user exists" lookup that doesn't reach LDAP, and
"user has nothing" is the same useful answer as "user doesn't exist."

## User Endpoints

Require valid JWT (any authenticated user). Filtered by the user's entitlements.

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/me/desktops` | List pools/desktops the user is entitled to |
| `POST` | `/me/desktops/{pool_id}/connect` | Request a desktop connection (the core broker call) |
| `DELETE` | `/me/sessions/{session_id}` | Disconnect my active session |
| `GET` | `/me/sessions` | List my active and recent sessions |

## Connection Brokering Flow

`POST /me/desktops/{pool_id}/connect` is the core broker endpoint:

```
User requests connection to pool "Engineering"
    │
    ├─ Verify user is entitled (entitlements table, check user + group memberships)
    │
    ├─ Pool type: persistent?
    │   ├─ YES: Find desktop assigned to this user in this pool
    │   │   ├─ Found, VM running → get VNC ticket from Proxmox
    │   │   ├─ Found, VM stopped → start VM, wait for agent, get ticket
    │   │   └─ Not found → clone from template, assign, start, get ticket
    │   │
    │   └─ NO (non-persistent): Find available spare desktop
    │       ├─ Spare available (status='available') → assign temporarily, get ticket
    │       ├─ No spare, under max_size → clone on demand, start, get ticket
    │       └─ No spare, at max_size → return 503 "pool full, try again later"
    │
    ├─ Create session record with connection_info
    │
    └─ Return to client:
        {
          "data": {
            "session_id": "uuid",
            "desktop_name": "ENG-003",
            "ticket": {
              "kind": "novnc",
              "websocket_url": "wss://pve-node:port/api2/json/nodes/{node}/qemu/{vmid}/vncwebsocket?port=...&vncticket=...",
              "password": "...",
              "cert_pem": "..."
            }
          },
          "error": null
        }
```

The `ticket` object is a typed sum — its `kind` field determines which other fields are present. For v0 only `"novnc"` is produced; the portal reads `kind` and dispatches to the appropriate renderer. The shape mirrors the `ConsoleTicket` type defined in `providers.md`.

### Per-Pool Assignment Semantics

A user may hold at most one desktop per pool they are entitled to. A user entitled to N pools may therefore hold up to N desktops concurrently (one per pool). Subsequent connect calls to the same pool return the user's existing desktop for that pool rather than provisioning a second one. This applies to both persistent and non-persistent pools; the distinction is only in lifecycle (persistent desktops survive session end; non-persistent are recycled).

### Async Destructive Operations

The following endpoints return **202 Accepted** and perform their work in the background. Current status is visible on the resource's `GET` endpoint:

- `DELETE /desktops/{id}` — destroys the VM and removes the record
- `POST /desktops/{id}/rebuild` — destroys and re-clones, preserving assignment
- `POST /pools/{id}/drain` — marks pool `draining`, stops provisioning
- `POST /pools/{id}/provision` — pre-provisions desktops toward `min_spare`

The connect endpoint (`POST /me/desktops/{pool_id}/connect`) is **synchronous**: it returns 200 with a ticket if an available desktop exists, or 503 `POOL_FULL` if not. It never triggers on-demand provisioning in M2; admins use `POST /pools/{id}/provision` to pre-warm a pool.

## Request/Response Conventions

- All responses wrapped in: `{ "data": ..., "error": null }` or `{ "data": null, "error": { "code": "...", "message": "..." } }`
- List endpoints support: `?limit=50&offset=0&sort=name&order=asc`
- Filter parameters are query string: `?status=active&pool_id=uuid`
- UUIDs for all resource IDs
- Timestamps in ISO 8601 / RFC 3339 (UTC)

## Error Codes

| HTTP | Code | Meaning |
|------|------|---------|
| 400 | `INVALID_REQUEST` | Malformed request or validation failure |
| 401 | `UNAUTHORIZED` | Missing or invalid JWT |
| 403 | `FORBIDDEN` | Valid JWT but insufficient permissions |
| 404 | `NOT_FOUND` | Resource doesn't exist |
| 409 | `CONFLICT` | VMID range overlap, duplicate name, etc. |
| 503 | `POOL_FULL` | Non-persistent pool at max capacity |
| 502 | `PROVIDER_ERROR` | Underlying hypervisor provider returned an error |
| 504 | `PROVIDER_TIMEOUT` | Hypervisor provider call timed out |

Error codes are deliberately provider-agnostic: `PROVIDER_ERROR` and `PROVIDER_TIMEOUT` cover the Proxmox provider today and any future provider (vSphere, Hyper-V, etc.) without churn.

### Error Response Shape and Admin-Only Details

Standard error envelope:

```json
{
  "data": null,
  "error": {
    "code": "PROVIDER_ERROR",
    "message": "Human-readable summary of what went wrong"
  }
}
```

For callers with `role=admin`, errors caused by the provider include an additional `details` object with provider-specific diagnostic information:

```json
{
  "data": null,
  "error": {
    "code": "PROVIDER_ERROR",
    "message": "Clone task failed",
    "details": {
      "provider": "proxmox",
      "raw": "task UPID:pve1:... failed: storage 'local-lvm' is full"
    }
  }
}
```

The `details` field is **omitted entirely** for non-admin users. This prevents leaking hypervisor internals, storage layout, and node names to regular users while keeping admins able to diagnose failures without grepping logs.
