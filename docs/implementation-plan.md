# OpenVDI Implementation Plan

## Repo Structure

```
OpenVDI/
├── README.md
├── LICENSE
├── docker-compose.yml               # Dev: Postgres, broker
├── .env.example
│
├── broker/                          # FastAPI backend
│   ├── pyproject.toml
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                  # FastAPI app + lifespan
│   │   ├── config.py                # Settings via pydantic-settings
│   │   ├── database.py              # SQLAlchemy async engine + session
│   │   ├── models/                  # SQLAlchemy ORM models
│   │   │   ├── __init__.py
│   │   │   ├── cluster.py
│   │   │   ├── template.py
│   │   │   ├── pool.py
│   │   │   ├── desktop.py
│   │   │   ├── session.py
│   │   │   ├── entitlement.py
│   │   │   └── audit.py
│   │   ├── schemas/                 # Pydantic request/response models
│   │   │   ├── __init__.py
│   │   │   ├── cluster.py
│   │   │   ├── template.py
│   │   │   ├── pool.py
│   │   │   ├── desktop.py
│   │   │   ├── session.py
│   │   │   └── entitlement.py
│   │   ├── api/                     # Route handlers
│   │   │   ├── __init__.py
│   │   │   ├── router.py            # Top-level router aggregation
│   │   │   ├── clusters.py
│   │   │   ├── templates.py
│   │   │   ├── pools.py
│   │   │   ├── desktops.py
│   │   │   ├── sessions.py
│   │   │   ├── user.py              # /me/* endpoints
│   │   │   └── auth.py              # Login, token refresh
│   │   ├── services/                # Business logic layer
│   │   │   ├── __init__.py
│   │   │   ├── pool_manager.py      # Pool CRUD + provisioning logic
│   │   │   ├── broker.py            # Connection brokering (the core)
│   │   │   ├── provisioner.py       # VM cloning, snapshot, lifecycle
│   │   │   ├── session_tracker.py   # Session state machine
│   │   │   ├── vmid_allocator.py    # VMID range management
│   │   │   └── auth_service.py      # LDAP/AD authentication
│   │   ├── providers/               # Hypervisor provider layer
│   │   │   ├── __init__.py          # Registry (register_provider, get_provider_class)
│   │   │   ├── base.py              # HypervisorProvider Protocol + shared types
│   │   │   ├── exceptions.py        # ProviderError hierarchy
│   │   │   └── proxmox/             # Proxmox provider implementation
│   │   │       ├── __init__.py
│   │   │       ├── provider.py      # ProxmoxProvider class (implements HypervisorProvider)
│   │   │       ├── client.py        # _ProxmoxClient low-level httpx wrapper
│   │   │       ├── params.py        # snake_case ↔ kebab-case translation
│   │   │       ├── types.py         # VMRef/TaskHandle encode/decode for Proxmox
│   │   │       └── exceptions.py    # Proxmox-local exceptions (extend ProviderError)
│   │   ├── workers/                 # Background tasks
│   │   │   ├── __init__.py
│   │   │   ├── pool_provisioner.py  # Maintain min_spare warm desktops
│   │   │   ├── session_monitor.py   # Poll guest agent, track sessions
│   │   │   ├── health_checker.py    # Cluster/node/storage health
│   │   │   └── task_tracker.py      # Track async provider tasks
│   │   └── middleware/
│   │       ├── __init__.py
│   │       ├── auth.py              # JWT middleware
│   │       └── audit.py             # Request audit logging
│   ├── scripts/
│   │   └── test_proxmox_provider.py # Milestone 1 acceptance test
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py
│       ├── test_broker.py
│       ├── test_provisioner.py
│       └── providers/
│           ├── conformance/         # Provider conformance suite (Milestone 4)
│           └── test_proxmox_unit.py # Unit tests with mocked httpx
│
├── portal/                          # React frontend
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── src/
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   ├── api/                     # API client hooks
│   │   ├── components/
│   │   │   ├── DesktopLauncher.tsx   # User's desktop list + connect
│   │   │   ├── NoVNCViewer.tsx       # Embedded noVNC component
│   │   │   ├── AdminDashboard.tsx
│   │   │   ├── PoolManager.tsx
│   │   │   ├── SessionList.tsx
│   │   │   └── ...
│   │   ├── pages/
│   │   └── hooks/
│   └── public/
│       └── novnc/                   # noVNC static assets
│
├── db/                              # Database scripts
│   ├── 001_schema.sql               # Initial schema (from database-schema.md)
│   ├── 002_seed_data.sql            # Dev seed data
│   └── drop_all.sql                 # Nuclear reset
│
├── deploy/                          # Deployment configs
│   ├── systemd/
│   │   └── lvm-lock-cleanup.service # Boot-time LVM lock cleanup for PVE nodes
│   ├── tofu/                        # OpenTofu modules (future)
│   └── ansible/                     # Playbooks (future)
│
└── docs/                            # Design documentation
    ├── architecture.md
    ├── database-schema.md
    ├── api-design.md
    ├── providers.md                 # HypervisorProvider interface spec
    ├── providers/
    │   └── proxmox.md               # Proxmox provider implementation doc
    ├── session-tracking.md
    └── implementation-plan.md       # This file
```

## Dependency Rules (enforced by convention)

Code under `services/`, `workers/`, `api/`, and `middleware/` imports from `providers/base` and `providers/exceptions` ONLY. It MUST NOT import from `providers/proxmox/` or any other concrete provider package. This keeps the broker hypervisor-agnostic.

Concrete providers register themselves in `providers/__init__.py` via `register_provider()`. The pool manager looks up and instantiates providers by `provider_type` from the clusters table.

## Dependencies

### Broker (Python)

```toml
[project]
name = "openvdi-broker"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.29",
    "httpx>=0.27",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "python-jose[cryptography]>=3.3",
    "ldap3>=2.9",
    "passlib[bcrypt]>=1.7",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "httpx",  # for TestClient
    "ruff>=0.4",
]
```

### Portal (Node/React)

```json
{
  "dependencies": {
    "react": "^18",
    "react-dom": "^18",
    "react-router-dom": "^6",
    "@tanstack/react-query": "^5",
    "@novnc/novnc": "^1.4"
  },
  "devDependencies": {
    "vite": "^5",
    "@vitejs/plugin-react": "^4",
    "typescript": "^5",
    "@types/react": "^18"
  }
}
```

## Implementation Milestones

### Milestone 1 — "It clones a VM through the provider interface" (Fresh restart)

**Goal:** Prove the provider abstraction is real by implementing it end-to-end for Proxmox and driving the test script through the `HypervisorProvider` surface — never calling `ProxmoxProvider` directly.

This milestone is being restarted. The prior attempt (Sonnet 4.6) was built against thin docs and tripped on three things now corrected: clone-mode semantics (`snapname` was being passed unnecessarily), the undocumented `exitstatus` field, and insufficient retry/timeout defaults. The revised docs codify the right answers; this milestone rebuilds against that foundation AND introduces the provider abstraction from the start (adding it retroactively after Milestone 2+ would be a rewrite).

**Preconditions (sysadmin, before coding):**

1. Service account and API token exist on Proxmox: `openvdi@pve!openvdi` with the privilege set documented in `providers/proxmox.md` → *Service Account Setup*.
2. A Proxmox VM template exists with:
   - `qm template <vmid>` has been run (or the VM was created as a template)
   - QEMU guest agent installed and enabled (`agent: 1` in config)
   - Minimum viable OS that boots cleanly
   - No `base` or other named snapshots (not needed; should not be present for clarity)
3. The target PVE node has `lvm-lock-cleanup.service` installed from `deploy/systemd/`, or the sysadmin is prepared to manually clear LVM locks if they appear.
4. An LVM-thin pool with free space for at least one linked clone (~10 GB metadata headroom is conservative).

**Deliverables (code):**

1. `broker/app/providers/base.py` — `HypervisorProvider` Protocol and shared types per `providers.md`: `VMRef`, `TaskHandle`, `ProviderCapabilities`, `ConsoleKind`, `NodeInfo`, `NodeStatus`, `StorageInfo`, `CloneRequest`, `PowerState`, `VMStatus`, `VMConfig`, `SnapshotInfo`, `GuestUser`, `OSInfo`, `NetworkInterface`, `ExecStatus`, `NoVNCTicket`, `WebMKSTicket`, `SpiceTicket`, `RDPTicket`, `ConsoleTicket`, `TaskState`, `TaskStatus`.

2. `broker/app/providers/exceptions.py` — `ProviderError` hierarchy per `providers.md`: `ProviderAuthError`, `ProviderNotFoundError`, `ProviderTimeoutError`, `ProviderTaskError`, `ProviderLockError`, `ProviderCapabilityError`.

3. `broker/app/providers/__init__.py` — the provider registry (`register_provider`, `get_provider_class`, `list_provider_types`).

4. `broker/app/providers/proxmox/exceptions.py` — Proxmox-local exception subclasses extending `ProviderError`.

5. `broker/app/providers/proxmox/params.py` — explicit snake_case ↔ kebab-case parameter translation per `providers/proxmox.md` → *API Parameter Name Translation*.

6. `broker/app/providers/proxmox/types.py` — `VMRef` and `TaskHandle` encode/decode helpers for Proxmox.

7. `broker/app/providers/proxmox/client.py` — low-level `_ProxmoxClient` httpx wrapper (auth header, request shaping, retry with exponential backoff, error mapping to `ProviderError` subclasses). Internal to the Proxmox provider.

8. `broker/app/providers/proxmox/provider.py` — `ProxmoxProvider` class implementing `HypervisorProvider` for the Milestone 1 method surface:
   - `capabilities`, `ping`, `close`
   - `list_nodes`, `get_node_status`, `list_storage`
   - `clone_vm` (linked clone from template; **no `snapname`, no `full`**)
   - `start_vm`, `stop_vm`, `shutdown_vm`, `destroy_vm` (with internal retry on lock errors)
   - `get_vm_status`, `list_vms`
   - `get_console_ticket` (noVNC branch; SPICE stubbed to raise `ProviderCapabilityError` for M1 if not exercised)
   - `agent_ping`, `agent_get_users`
   - `get_task_status`, `wait_for_task` (default timeout 600s)
   - Registered at module import time with `@register_provider`.

9. `broker/app/config.py` — Pydantic-settings config loading from env/`.env`:
   - `proxmox_api_url`, `proxmox_token_id`, `proxmox_token_secret`, `proxmox_verify_ssl`
   - `proxmox_default_node`, `proxmox_template_vmid`, `proxmox_test_vmid`, `proxmox_target_storage`

10. `db/001_schema.sql` — Full database schema from `database-schema.md`. Not exercised in this milestone, but present so the next milestone doesn't repeat work. Schema includes `clusters.provider_type` and `pools.provider_config` columns.

11. `docker-compose.yml` — PostgreSQL + pgAdmin for dev. Broker itself runs on the host for iteration speed.

12. `broker/scripts/test_proxmox_provider.py` — Standalone acceptance test. **Not** a pytest; a script with clear stdout logging. Hard-coded to use config values. **Drives the test exclusively through the `HypervisorProvider` interface** — no direct reference to Proxmox API paths or `_ProxmoxClient`. Steps, each logging success/failure:
    - Construct `ProxmoxProvider` via the registry (`get_provider_class("proxmox")(...)`)
    - `provider.ping()`
    - `provider.list_nodes()`; confirm configured node is online
    - `provider.get_vm_status(template_ref)` and confirm `is_template=True`
    - `provider.list_vms(node=...)`; verify test_vmid not present
    - `provider.clone_vm(CloneRequest(...))` → `TaskHandle`
    - `provider.wait_for_task(handle, timeout_seconds=600)`
    - `provider.start_vm(test_ref)` → wait for task
    - Poll `provider.agent_ping(test_ref)` up to 90 seconds
    - `provider.agent_get_users(test_ref)` and log the result
    - `provider.get_console_ticket(test_ref, ConsoleKind.NOVNC)` and log the `websocket_url`
    - `provider.shutdown_vm(test_ref, timeout_seconds=120, force=True)` → wait
    - Poll `provider.get_vm_status(test_ref).power_state` until `stopped`
    - `provider.destroy_vm(test_ref)` → wait
    - Verify VM gone from `provider.list_vms`
    - `provider.close()`
    - Print a final PASS/FAIL summary with per-step timing

**Acceptance criteria:**
- Test script runs end-to-end on Alton's Proxmox server with no manual intervention.
- Test script imports from `app.providers.base` and `app.providers` (registry) only — it does NOT import `ProxmoxProvider` or any Proxmox-internal modules directly.
- No `snapname` parameter anywhere in the clone path.
- Clone produces a linked clone (verified out-of-band via `qm config <vmid>` showing `scsi0: ...,base-9001-disk-0` style reference, not a copied disk).
- Destroy succeeds on the first attempt in normal conditions; retry path exercised via a targeted check (destroy while a contrived lock is held).
- LVM lock orphan, if encountered, surfaces as `ProviderLockError` and is documented as operator action — not silently retried indefinitely.

**Explicitly out of scope for this milestone:**
- Snapshots (creation/rollback) — introduced in Milestone 2 for non-persistent pools.
- Database operations — schema exists, but nothing writes to it yet.
- FastAPI app, routes, auth — no HTTP surface yet.
- Background workers.
- Provider conformance test suite — added in Milestone 4.
- Non-linked-clone pathways.
- Second providers (vSphere, Hyper-V) — interface is ready; no second implementation in v0.

### Milestone 2 — "Broker assigns a desktop" (Weekend 2)

**Goal:** Deliver the full HTTP broker surface and the service layer behind it. All M2 flows are exercised end-to-end through curl; no React portal and no real auth yet (header-based dev auth stands in for JWT/LDAP). At the end of M2, an admin can register a cluster, register a template, create a pool, pre-provision desktops, and a user can hit `POST /me/desktops/{pool_id}/connect` and receive a noVNC ticket that the M1 test VM would have accepted.

**Deliverables — data layer:**
- `broker/app/database.py` — SQLAlchemy 2.x async engine, session factory, `get_db_session` dependency (session-per-request).
- `broker/app/models/` — one ORM model per file (`cluster.py`, `template.py`, `pool.py`, `desktop.py`, `session.py`, `entitlement.py`, `audit.py`). `updated_at` via `onupdate=func.now()`. Models are pure data — they do NOT import from `providers/`.
- `broker/app/schemas/` — Pydantic schemas with `*Create`, `*Update`, `*Read` trio per resource, one file per resource. Includes generic `APIResponse[T]` wrapper and a `PaginationParams` base for `Depends()`.
- `broker/app/crypto.py` — Fernet encryption helpers (`encrypt_secret`, `decrypt_secret`) keyed on `OPENVDI_ENCRYPTION_KEY`. Includes a one-shot key-generation CLI (`python -m app.crypto generate-key`).
- `db/001_schema.sql` — extended with `pending` in `cluster_status` enum and explicit `assignment_type` documentation (`persistent` | `floating`).
- `db/002_seed_data.sql` — placeholder cluster rows stay in `pending` until their first live ping from the broker.
- `scripts/db-reset.sh` — runs `drop_all.sql` → `001_schema.sql` → `002_seed_data.sql`. Used by the M2 end-to-end test harness.

**Deliverables — provider extensions:**
- `ProxmoxProvider.create_snapshot`, `rollback_snapshot`, `list_snapshots`, `delete_snapshot` — already in the `HypervisorProvider` Protocol; implemented on the concrete class in M2 per `providers/proxmox.md` → *Snapshots*.
- `ProxmoxProvider.configure_vm` — implemented for the M2 pool-override flow (post-clone, pre-first-start).

**Deliverables — services:**
- `broker/app/services/vmid_allocator.py` — lowest-available VMID allocation within pool range; Postgres transaction advisory lock keyed per-pool to serialize concurrent allocations; one-shot retry on Proxmox VMID collision. Pool-create-time Proxmox scan to reject ranges that already contain VMs.
- `broker/app/services/provisioner.py` — full provisioning cycle: clone → apply overrides → start → wait for agent → [non-persistent: shutdown → create `openvdi-base` → start] → mark `available`. DB row created in `provisioning` state before clone so the VMID is reserved. Failed provisioning leaves VM intact and marks desktop row `error` with `error_message` — no auto-cleanup.
- `broker/app/services/broker.py` — connect flow for `POST /me/desktops/{pool_id}/connect`. Per-user-per-pool advisory lock during connect. Persistent: find existing assignment or 503 (M2 does not clone on connect — pre-provision required). Non-persistent: find available spare, mark `floating` assignment, or 503 if none. Session row written in `connecting` before the provider ticket call; promoted to `active` once the ticket is in hand.
- `broker/app/services/session_tracker.py` — thin synchronous state machine: `transition_to_active`, `transition_to_disconnected`, `transition_to_ended`. `ended` clears `connection_info` in a single UPDATE. No polling loop.
- `broker/app/services/auth_service.py` — header parser that produces the `User` object attached to `request.state.user`. Pattern is JWT-ready: M4 swaps the middleware, handlers and downstream deps are unchanged.
- `broker/app/services/audit_service.py` — `log_business_event(actor, action, resource_type, resource_id, details)` for service-layer audit writes (e.g. `broker.connect`, `broker.session.end`).
- `broker/app/services/task_tracker.py` — helpers for the background-task-polls-DB pattern. On broker startup, inspects desktops with non-null `pve_task_upid` and resumes polling.

**Deliverables — HTTP layer:**
- `broker/app/middleware/auth.py` — header-based dev auth (`X-Dev-User`, `X-Dev-Groups`, `X-Dev-Role`). Broker refuses to start in dev-auth mode unless `OPENVDI_AUTH_MODE=dev` is set explicitly.
- `broker/app/middleware/audit.py` — HTTP-level audit rows for every admin mutation (POST/PUT/DELETE on admin endpoints). Explicit redaction list: `token_secret`, `password`, any `SecretStr` field.
- `broker/app/main.py` — FastAPI app with lifespan handler that loads clusters, constructs providers into `app.state.providers`, fires background cluster ping tasks, and cleanly closes providers at shutdown. `get_provider(cluster_id)` dependency. Global exception handlers mapping `ProviderError` subclasses to `PROVIDER_ERROR` / `PROVIDER_TIMEOUT` / `POOL_FULL` etc. per `api-design.md`. Response envelope (`APIResponse[T]`) applied uniformly.
- `broker/app/api/` — separate `admin_router` (`/api/v1/…`) and `user_router` (`/api/v1/me/…`) with their own dependency chains. Admin routers: `clusters.py`, `templates.py`, `pools.py`, `desktops.py`, `sessions.py`, `entitlements.py`, `audit.py`, `dashboard.py`. User router: `user.py` (`/me/*`). `POST /clusters` and `PUT /clusters/{id}` validate via `provider.ping()` before persisting. `POST /templates` does light validation via `get_vm_status`. Async destructive ops (`DELETE /desktops/{id}`, `POST /desktops/{id}/rebuild`, `POST /pools/{id}/drain`, `POST /pools/{id}/provision`) return 202 Accepted and are orchestrated via FastAPI `BackgroundTasks`.

**Deliverables — testing:**
- `broker/scripts/test_m2_end_to_end.sh` — curl-driven walkthrough. Runs `db-reset.sh`, starts the broker, walks: `PUT /clusters/{seed_id}` with real creds → register template → create pool → pre-provision → connect → verify desktop and snapshot present in Proxmox → disconnect → destroy. Prints PASS/FAIL per step like the M1 script.

**Validation:** The M2 end-to-end test script exercises the full flow. Manual acceptance checkpoints as it runs:
1. After `db-reset.sh`, the seeded cluster row is in `status='pending'`.
2. `PUT /clusters/{seed_id}` with real credentials triggers a `ping()`; on success the cluster flips to `active` and its provider is constructed in-process.
3. Template registration calls `provider.get_vm_status` and rejects non-templates.
4. Pool creation scans Proxmox for any existing VMs in the declared VMID range and rejects the pool if any are found.
5. `POST /pools/{id}/provision` returns 202 immediately; the desktops appear in `provisioning`, transition through `available`, and acquire the `openvdi-base` snapshot (non-persistent pools only) visible in Proxmox.
6. `POST /me/desktops/{pool_id}/connect` returns a nested-ticket response matching the shape in `api-design.md`; the websocket URL is reachable from a browser; `desktops.assigned_user` is set for the connected user.
7. `DELETE /me/sessions/{id}` transitions the session to `ended`, clears `connection_info`, and (for non-persistent) clears `assigned_user`.
8. Admin `DELETE /desktops/{id}` returns 202 and the VM is gone from Proxmox once the background task completes.
9. Connecting without `X-Dev-Role=admin` to an admin endpoint returns 403; a non-admin caller's error response contains no `details` field.

**Explicitly out of scope for M2:**
- Real LDAP/JWT auth → M4.
- Refresh-on-logoff worker for non-persistent pools → M4. (Snapshot is created; recycle is not.)
- Pool provisioner background worker → M4. M2 is lazy/on-demand via the provision endpoint.
- Session monitor (guest agent polling loop) → M4.
- Health checker worker → M4.
- React portal → M3.
- Provider conformance test suite → M4.
- Alembic migrations → M4. Raw SQL for M2.
- JSON structured logging → M4+. Human-readable logs for M2.
- Dashboard aggregate caching → M4+.
- Second hypervisor provider → post-v0.

### Milestone 3 — "I can connect from a browser" (Weekend 3)

**Goal:** First end-to-end demo — browser → FastAPI → provider → VM console. The user logs in, picks an entitled pool, clicks Connect, and operates a real Windows or Linux desktop in their browser.

**Deliverables:**

- `portal/` — Vite + React + TypeScript scaffold (M3-01) with a Tailwind theme bridge that maps every Praxova design-system role token to a Tailwind utility class. Vanilla Tailwind defaults (`bg-amber-500`, etc.) do not compile, by design.
- `portal/src/api/client.ts` + `portal/src/api/errors.ts` — `BrokerClient` class wrapping fetch (M3-02) with typed envelope handling, `BrokerError` class, transport-layer error normalization, M3-04's TanStack Query `defaultError` register declaration, and M3-07's `brokerErrorCode` helper.
- `portal/src/auth/AuthContext.tsx` + `portal/src/auth/ProtectedRoute.tsx` + `portal/src/lib/theme.ts` (M3-03) — header-based dev auth (X-Dev-User / X-Dev-Groups / X-Dev-Role) with a JWT-ready seam for M4. Theme module reads prefers-color-scheme, persists override to localStorage, applies via `[data-theme]` attribute. AppShell header per design-system §8.10.1 with brand mark, nav, username, theme toggle, logout.
- `portal/src/pages/LoginPage.tsx` (M3-03) — username, groups (CSV), role pill-radio. Submission writes to AuthContext + localStorage and bounces to /desktops.
- `portal/src/pages/DesktopsPage.tsx` + `portal/src/components/PoolCard.tsx` + `portal/src/components/StatusBadge.tsx` (M3-04) — TanStack Query bound to `GET /me/desktops`. Pool cards render `display_name` (NEVER `name` slug), description, type pill, assignment summary if present, Connect/Resume button. Loading skeleton, error state with Retry, empty state.
- `portal/src/components/NoVNCViewer.tsx` + `portal/src/types/novnc.d.ts` (M3-05) — pure presentational viewer wrapping `@novnc/novnc@^1.4`. StrictMode-safe RFB lifecycle, canvas-stacking-defense via `replaceChildren`, callback ref-mirror to avoid effect-deps churn. `forwardRef` exposes `sendCtrlAltDel`. Vitest-tested with mocked RFB extending real EventTarget.
- `portal/src/pages/ConsolePage.tsx` + `portal/src/components/ConsoleToolbar.tsx` + `portal/src/api/connect.ts` + `portal/src/api/sessions.ts` (M3-06) — connect mutation, disconnect mutation, three cleanup paths (explicit Disconnect button, SPA-nav cleanup, tab-close beforeunload — all fenced by a single `disconnectFiredRef`). Connection state machine: connecting → connected → disconnecting → disconnected | error. Auto-navigate on user-initiated disconnect; stay-on-page for unexpected events.
- `portal/src/pages/SessionsPage.tsx` + `portal/src/components/SessionRow.tsx` + `portal/src/lib/time.ts` (M3-07) — sessions table with two-state filter (Active | All), per-row Disconnect for active sessions, orphan handling for sessions whose backing desktop has been deleted. `formatRelativeTime` lifted from M3-04 PoolCard.
- `portal/playwright.config.ts` + `portal/e2e/*` (M3-08) — Playwright smoke suite covering launcher, connect flow, and theme toggle. Asserts canvas exists with non-zero dimensions and the connection-state indicator transitions through "Connecting" → "Connected" — a transitive proof that RFB's connect event fired.

**Validation:**

1. `pnpm install` resolves clean against the M3 lockfile. `pnpm typecheck`, `pnpm lint`, `pnpm test`, `pnpm build` all pass.
2. `pnpm dev` starts the Vite server on :5173 with the broker proxy on /api/* targeting :8080.
3. Login as a user entitled to one or more pools. Launcher renders one card per pool with display_name (not slug), description, status badge, pool type pill, and Connect/Resume button.
4. Click Connect. Console route renders; toolbar status transitions through "Connecting…" → "Connected to {desktop}". Canvas paints the VM's desktop. Keyboard and mouse input flow to the VM. Send Ctrl+Alt+Del triggers the secure attention sequence (Windows) or the equivalent on Linux.
5. Click Disconnect. Page navigates to /desktops with the launcher's TanStack cache refetched. For non-persistent pools, the assigned-desktop summary clears; for persistent, the assignment remains with status "disconnected".
6. Navigate to /sessions. Recent disconnect appears under the "All" filter with the "Disconnected" status badge.
7. Toggle dark mode. `[data-theme]` flips on `<html>`; cards and badges re-render with dark-mode tokens; brand mark swaps to the dark variant.
8. Logout. /desktops becomes inaccessible without re-authenticating; the auth user is cleared from localStorage; the TanStack Query cache is cleared (no stale data flash on next login).
9. The Playwright smoke suite (`pnpm e2e`) passes against a real broker + Proxmox cluster.
10. The manual acceptance checklist in `portal/README.md` is walked through end-to-end with no failures.

**Explicitly out of scope for this milestone:**

- Real LDAP / JWT authentication (M4).
- Admin dashboard, admin endpoints, admin-only routes (M4).
- Pool / template / cluster CRUD UI for admins (M4).
- Provider conformance test suite (M4).
- Background workers — pool provisioner, session monitor, health checker, task tracker (M4).
- Multi-tab session-tracking improvements (M4 session monitor handles dangling sessions until then).
- KasmVNC display protocol (v1).
- Mobile / tablet viewport polish — cosmetic in v0.
- Bundle-size code-splitting; the console route's noVNC payload is in the main bundle (M5+).
- Real-time updates via websocket; the launcher is fetch-on-mount with TanStack staleTime (M5+).

### Milestone 4 — "Production-ready v0"

**Goal:** Real LDAP/JWT auth, the five background workers, the refresh-on-logoff cycle, the admin portal, and the provider conformance suite. After this milestone OpenVDI v0 ships.

**Deliverables (v0):**

- **Auth & migrations**
  - LDAP search-and-bind via service account (`broker/app/services/ldap_service.py`).
  - JWT issuance + validation (HS256, 15-min access + 24-h refresh in DB-backed `auth_tokens` table).
  - HttpOnly + Secure + SameSite=Strict refresh cookie scoped to `/api/v1/auth`.
  - `OPENVDI_AUTH_MODE=jwt|dev` switch; broker refuses to start without explicit setting.
  - Alembic introduced. Baseline `0001_baseline_m3` + `0002_auth_tokens`. Raw SQL files retained as historical artifacts only.
- **Workers**
  - `app/workers/` framework with leader election via `pg_try_advisory_lock` per worker (multi-broker-ready from day one).
  - `session_monitor` (15s) — guest-agent polling + 3-tick logoff debounce.
  - `pool_provisioner` (30s) — warm-spare maintenance.
  - `task_tracker` (5s) — replaces M2's BackgroundTasks-driven UPID polling.
  - `health_checker` (60s) — cluster ping + W13 cross-broker config sync + stuck-provisioning recovery.
  - `audit_retention` (24h, ±2h jitter) — daily prune of rows past `OPENVDI_AUDIT_RETENTION_DAYS` (default 90).
- **Refresh-on-logoff cycle**
  - `provisioner.refresh_desktop` — non-persistent rollback to `openvdi-base`.
  - `provisioner.delete_desktop_on_logoff` — `pool.delete_on_logoff=true` path.
- **Observability**
  - JSON structured logging (`OPENVDI_LOG_FORMAT=json|text`).
  - `X-Request-ID` middleware + ContextVar-propagated request_id in every log line.
- **Provider conformance suite**
  - `broker/tests/providers/conformance/` — pytest with `--provider=proxmox` flag. Live cluster requirement; not in CI.
  - 5 test files (~33 tests) covering capabilities, lifecycle, snapshots, tasks, guest agent.
  - Provider gap-fill: `reboot_vm`, `agent_get_osinfo`, `agent_get_network`, `agent_exec`, `agent_exec_status`.
- **Admin portal**
  - LDAP login via `/login` form replacing M3 dev-auth.
  - Bearer-token lifecycle in `BrokerClient`: 401 → `/auth/refresh` → replay; refresh-failure → bounce to `/login`. De-duplicated concurrent refreshes.
  - `<AdminRoute>` + Admin ▾ header dropdown for role-gated nav.
  - Pages: dashboard (4 cards), clusters CRUD, templates CRUD + validate, pools CRUD + provision/drain + entitlements, desktops list + actions + drawer, sessions list + force-disconnect + drawer, audit viewer + drawer + pagination.
  - `DataTable` + `FormField` primitives at `components/admin/`; `Section` / `Field` / `CopyableField` extracted in M4-24.

**Validation:**

1. Login as a real LDAP user. Access protected pages. Logout revokes the refresh token; subsequent API calls return 401.
2. Workers visible in the broker logs. Each ticks at its declared cadence; leader election survives a broker restart (advisory lock auto-releases on connection close).
3. Conformance suite passes against the test PVE cluster (`pytest broker/tests/providers/conformance/ --provider=proxmox`).
4. Admin Playwright spec passes (`pnpm exec playwright test admin-flow` — see `portal/README.md` → "M4 admin smoke test" for env-var setup).
5. `m4-complete` git tag applied at HEAD.

**Out of scope (deferred to v1+):**

KasmVNC display protocol, WAN reverse proxy, code-splitting, real-time portal updates (websockets), mobile/tablet polish, broker rate limiting, multi-tenant isolation, MFA, second hypervisor provider, OpenTelemetry tracing, audit shipping to external SIEM.

**Tag:** `m4-complete`

### Milestone 5+ — Polish and Extend

- Audit logging middleware
- Pool drain / maintenance mode
- Desktop rebuild (destroy + re-clone preserving assignment)
- Template validation endpoint (verify agent installed, is template, snapshot OK)
- Health checker worker
- Capacity dashboard with per-provider node metrics
- Error recovery (stuck desktops, orphan VMs)
- Multi-node placement logic (least-loaded node selection)
- `docker-compose.yml` for full stack (Postgres + broker + portal)
- Comprehensive error handling and user-friendly error messages
- Rate limiting on broker endpoints
- Second provider implementation (vSphere or XCP-ng, when a customer or validation partner materializes)

## noVNC Integration Notes

Proxmox's VNC WebSocket proxy (`vncproxy`) binds to the Proxmox node's IP on a random high port. The browser must reach this directly.

**LAN (MVP):** Browser connects directly to `wss://proxmox-node:port/api2/json/nodes/{node}/qemu/{vmid}/vncwebsocket?port={port}&vncticket={ticket}` — verified against PVE 9.x spec; reverify on Milestone 3 during the first real browser connect.

**WAN (future):** Requires a reverse proxy or tunnel. When KasmVNC is integrated (v1), the architecture changes — KasmVNC runs inside the VM and exposes its own WebSocket endpoint, which can be more easily proxied.

## Development Setup

```bash
# Clone the repo
git clone git@github.com:Praxova/OpenVDI.git
cd OpenVDI

# Start Postgres
docker-compose up -d

# Initialize database (not strictly needed for Milestone 1, but harmless)
psql -h localhost -U openvdi -d openvdi -f db/001_schema.sql

# Set up broker
cd broker
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Configure
cp ../.env.example ../.env
# Edit .env with Proxmox credentials, template VMID, test VMID

# Run Milestone 1 acceptance test
python scripts/test_proxmox_provider.py

# Run broker (Milestone 2+)
uvicorn app.main:app --reload --port 8080

# Set up portal (separate terminal, Milestone 3+)
cd portal
npm install
npm run dev
```

## Key Design Decisions Log

| Decision | Rationale | Date |
|----------|-----------|------|
| Proxmox over VMware | 5-6x Broadcom price hikes, Horizon future uncertain, Proxmox API is solid | 2026-03-31 |
| FastAPI over Django/Flask | Async native, consistent with Praxova stack, auto-docs | 2026-03-31 |
| PostgreSQL over SQLite | JSONB, concurrent access, production-grade | 2026-03-31 |
| Reserved VMID ranges | Visual grouping in Proxmox UI, cleaner management | 2026-03-31 |
| noVNC for v0 | No client install, browser-native, broad provider support | 2026-03-31 |
| QEMU guest agent for session tracking | Built into Proxmox, no custom agent needed for MVP | 2026-03-31 |
| Don't compete with FSLogix | Free with Windows licensing, solves profile persistence already | 2026-03-31 |
| Snapshot rollback for nonpersistent refresh | Proxmox native, fast, reliable | 2026-03-31 |
| React for portal | Rich SPA needed for noVNC embed + admin dashboard | 2026-03-31 |
| Monorepo | Tightly coupled for MVP, simplifies development | 2026-03-31 |
| Raw SQL over Alembic for now | Speed of development, migrate later | 2026-03-31 |
| Linked clones from template current state (no snapname) | Proxmox default for template sources; avoids the Milestone 1 confusion where a named "base" snapshot was created unnecessarily on the template | 2026-04-21 |
| `openvdi-base` snapshot lives on desktop VMs, not templates | Clear separation: templates are immutable sources, per-desktop snapshots are rollback points for non-persistent pools | 2026-04-21 |
| Document the `exitstatus` task field as a spec quirk | Not in OpenAPI spec but returned by real API; critical for task success detection | 2026-04-21 |
| Default `wait_for_task` timeout raised to 600s | 120s is too tight for clone tasks on LVM-thin; callers override for shorter ops | 2026-04-21 |
| Explicit snake_case ↔ kebab-case param mapping | Silent failure mode when mixing `generate_password` vs `generate-password`; explicit mapping catches typos | 2026-04-21 |
| Milestone 1 restarted cleanly | Prior attempt was <1 weekend of work against thin docs; cost of restart is lower than cost of incremental fix of wrong foundations | 2026-04-21 |
| Hypervisor provider abstraction from day 1 | Avoids Omnissa/Horizon trap (tight vCenter coupling = rewrite to move); retrofitting post-M2 would be a rewrite; cost to build now is ~200 lines of typed shared models and a folder layout | 2026-04-21 |
| v0 requires noVNC-capable providers only | Scope-constrains the console renderer in the portal to one implementation; data shapes (WebMKS, SPICE, RDP) in place for future providers | 2026-04-21 |
| Lowest-common-denominator avoided via `ProviderCapabilities` + `provider_opts` | Provider-specific strengths (SDN, resource pools, templates model) preserved through a capabilities declaration and opaque provider-options blob, rather than being flattened out of the interface | 2026-04-21 |
| Header-based fake auth for M2 dev | Unblocks API development without waiting on LDAP/JWT stack; same `User` shape as the eventual JWT path so M4 middleware swap is transparent to handlers | 2026-04-23 |
| Refresh-on-logoff deferred to M4 | The M2 provisioner creates the `openvdi-base` snapshot but no worker polls the guest agent yet; keeping the worker out of M2 keeps the milestone a pure HTTP/service deliverable | 2026-04-23 |
| Lazy provisioning in M2, no warm-spare worker | Admin pre-provisions via `POST /pools/{id}/provision`; connect is synchronous against existing `available` desktops or 503. Keeps M2 testable via curl without a loop thread to reason about | 2026-04-23 |
| Async destructive ops return 202 | Clone-on-demand and destroy are long (seconds to minutes); surfacing a job status on the resource's `GET` endpoint avoids inventing a jobs-API in M2 | 2026-04-23 |
| Session row written in `connecting` before the ticket call | Ticket creation is the riskiest network call in the connect path; having the session row pre-written gives the audit trail and state-machine a clean home even if the ticket call fails | 2026-04-23 |
| Per-user-per-pool advisory lock on connect | User double-clicking "connect" shouldn't mint two sessions for the same pool; lock keyed on `hashtext('user:<user>:pool:<pool>')` serializes without blocking other users | 2026-04-23 |
| Per-pool advisory lock on VMID allocation | Concurrent provision calls for the same pool must not collide on a VMID; scope the lock to the pool, not globally | 2026-04-23 |
| Error `details` admin-only | Provider failures often carry node names, storage IDs, and stack traces. Admins need those to debug; users shouldn't see them | 2026-04-23 |
| Fernet encryption for `token_secret` at rest | Symmetric, key in env var, drop-in via `cryptography` — DB dumps are safer and rotating keys requires only re-encrypting the `clusters` table | 2026-04-23 |
| Cluster lifespan fully handled in M2 | `PUT /clusters/{id}` transactionally closes the old provider and constructs the new; `DELETE /clusters/{id}` rejects when pools still reference the cluster; startup tolerates offline clusters by marking `status='offline'` and continuing | 2026-04-23 |
| New cluster enum value `pending` | Clusters start in `pending` pre-first-ping; a background task flips them to `active`/`offline` based on the first `provider.ping()` result. Avoids the wrong implication that a just-inserted row is definitely alive | 2026-04-23 |
| Audit at two layers (middleware + service) | HTTP middleware catches CRUD mutations with redaction; service layer writes domain events (`broker.connect`, `broker.session.end`) that have no clean HTTP mapping | 2026-04-23 |
| No users table, AD is source of truth | Entitlements match usernames/group names from the auth context directly; keeping OpenVDI out of identity management | 2026-04-23 |
| Praxova design-system as visual contract | Praxova products converge on one design language; OpenVDI portal references role tokens (`--color-action-primary`, `--space-4`, etc.) and brand SVGs from `/home/alton/Documents/Praxova/praxova-design-system/`, never raw values | 2026-04-27 |
| Tailwind theme bridge over vanilla utilities | A restricted `tailwind.config.js` maps each role token to a single Tailwind utility class. `bg-amber-500` doesn't compile — surfaces drift the moment it would otherwise creep in | 2026-04-27 |
| Browser-direct WebSocket to PVE for noVNC v0 | Vite dev proxy is HTTP-only; PVE's vncwebsocket is wss://. Running both behind one proxy buys nothing in dev and would mask the production-equivalent CORS/cert path. M3 documents that PVE's self-signed cert must be browser-trusted | 2026-04-27 |
| Header-based dev auth in M3 | M3 lands without LDAP/JWT to keep frontend velocity unblocked. The middleware seam M2-04 introduced has the same `User` shape JWT will produce in M4; only the middleware swaps | 2026-04-27 |
| StrictMode-safe one-shot connect via `didMountRef` | React 18 dev double-effect would fire two `POST /me/desktops/{id}/connect` calls; the broker's per-user-per-pool advisory lock serializes them but burns an unnecessary VNC ticket on Proxmox. The ref guard makes dev exactly-once | 2026-04-27 |
| Three keepalive-DELETE cleanup paths fenced by one ref | Explicit Disconnect button + SPA-nav cleanup + beforeunload-tab-close all converge on `fetch(DELETE, { keepalive: true })`, fenced by `disconnectFiredRef` so only one fires per page lifecycle. M4 session monitor recycles whatever escapes | 2026-04-27 |
| Connect-button always enabled regardless of pool status | Pool state at launcher-paint time can be stale within seconds. StatusBadge communicates state honestly; broker is the source of truth on whether a click succeeds. M3-06 surfaces 503/409 inline | 2026-04-27 |
| Discriminated-union ticket type with single v0 renderer | `ConsoleTicket = NoVNCTicket | WebMKSTicket | SpiceTicket | RDPTicket`. v0 produces `novnc` only; the union shape lets future renderers (KasmVNC v1) drop in without a backend change | 2026-04-27 |
| Playwright canvas assertion is dimensional, not visual | Pixel content varies every connection (cursor blink, idle wallpaper, etc). Canvas-exists + non-zero w/h + transitive RFB.connect proof via toolbar status is the right signal for the smoke gate | 2026-04-27 |
