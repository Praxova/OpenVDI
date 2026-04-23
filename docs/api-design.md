# OpenVDI API Design

## Overview

FastAPI REST API serving both the admin dashboard and user-facing web portal. JWT-based authentication with AD/LDAP backend. All endpoints under `/api/v1`.

## Authentication

- Users authenticate via `POST /api/v1/auth/login` with AD/LDAP credentials
- Server returns a JWT access token (short-lived, ~15 min) and refresh token (~24 hours)
- All subsequent requests include `Authorization: Bearer <token>`
- Token contains: `sub` (username), `groups` (AD group memberships), `role` (admin/user)
- Admin role determined by membership in a configurable AD group

### M2 Dev-Mode Authentication

M2 ships **header-based fake authentication** to unblock API development before the LDAP/JWT stack lands in M4. The middleware reads three headers on every request and constructs the same in-memory `User` shape that the JWT path will produce later:

| Header | Meaning |
|--------|---------|
| `X-Dev-User` | Username (e.g. `jsmith`) |
| `X-Dev-Groups` | Comma-separated AD group names (e.g. `Engineering,VPN Users`) |
| `X-Dev-Role` | `admin` or `user` |

Admin endpoints assert `role == admin`; user endpoints assert `role` is set. Missing or malformed headers produce a 401. In M4 this middleware is replaced with JWT validation — the downstream handlers and dependencies read from `request.state.user` in both modes and are unaffected by the switch.

This is a **development convenience, not a security boundary**. The broker MUST refuse to start in dev-auth mode unless an explicit `OPENVDI_AUTH_MODE=dev` env var is set.

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
