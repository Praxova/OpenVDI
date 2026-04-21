# OpenVDI API Design

## Overview

FastAPI REST API serving both the admin dashboard and user-facing web portal. JWT-based authentication with AD/LDAP backend. All endpoints under `/api/v1`.

## Authentication

- Users authenticate via `POST /api/v1/auth/login` with AD/LDAP credentials
- Server returns a JWT access token (short-lived, ~15 min) and refresh token (~24 hours)
- All subsequent requests include `Authorization: Bearer <token>`
- Token contains: `sub` (username), `groups` (AD group memberships), `role` (admin/user)
- Admin role determined by membership in a configurable AD group

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
          "session_id": "uuid",
          "protocol": "novnc",
          "websocket_url": "wss://pve-node:port/websockify?token=...",
          "password": "...",
          "desktop_name": "ENG-003"
        }
```

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
| 502 | `PROXMOX_ERROR` | Proxmox API returned an error |
| 504 | `PROXMOX_TIMEOUT` | Proxmox API call timed out |
