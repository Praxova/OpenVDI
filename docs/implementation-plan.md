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

**Goal:** First end-to-end demo: browser → FastAPI → provider → VM console.

**Deliverables:**
- `portal/` — Basic React app with Vite
- `portal/src/components/NoVNCViewer.tsx` — Embedded noVNC component, accepting a `NoVNCTicket` shape
- `portal/src/components/DesktopLauncher.tsx` — Desktop list + connect button
- `portal/src/api/` — API client using @tanstack/react-query
- Proxy config (Vite dev proxy to FastAPI)

**Validation:** Open browser → see available desktop → click connect → noVNC console appears in browser showing the VM desktop.

### Milestone 4 — "Sessions work, admins can see, conformance is real" (Weekend 4)

**Goal:** Session tracking, background workers, admin visibility, auth, AND the first formal provider conformance suite.

**Deliverables:**
- `broker/app/workers/session_monitor.py` — Guest agent polling loop
- `broker/app/workers/pool_provisioner.py` — Warm spare management
- `broker/app/workers/task_tracker.py` — Async provider task tracking
- `broker/app/services/auth_service.py` — LDAP/AD authentication
- `broker/app/middleware/auth.py` — JWT token middleware
- `broker/app/api/auth.py` — Login endpoint
- `portal/src/components/AdminDashboard.tsx` — Pool status, session list
- `portal/src/pages/LoginPage.tsx`
- `broker/tests/providers/conformance/` — Provider-agnostic test suite that any provider implementation must pass against a live test cluster. Tests assert behavior of the `HypervisorProvider` interface (clone → start → agent_ping → destroy round-trip, snapshot lifecycle, task success/failure paths, lock error handling). The Proxmox provider must pass it.

**Validation:**
1. Login with AD credentials
2. User sees only entitled pools
3. Connect to desktop via noVNC
4. Admin dashboard shows active session with guest agent data (os_user, IP)
5. User logs off OS → session monitor detects → desktop refreshed/recycled
6. `pytest broker/tests/providers/conformance/ --provider=proxmox` passes end-to-end

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
