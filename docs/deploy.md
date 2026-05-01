# OpenVDI Deployment

This document covers the operational decisions OpenVDI v0 forces on a deployer: production topology, the same-origin requirement that comes with HttpOnly refresh-token cookies, multi-broker posture, TLS, database migrations, logging, and backup. M4 is the milestone that makes these decisions concrete; until M4 ships, much of this is forward-looking.

For development setup, see `portal/README.md` (frontend dev) and `broker/README.md` (broker dev). This doc is for production.

## Production Topology

```
Browsers
   │
   ▼ HTTPS :443
┌─────────────────────────────────────────┐
│  TLS terminator (Caddy / nginx / etc.)  │   one origin: serves portal static
│                                          │   AND proxies /api/* to broker
│   ┌──────────────────────────────────┐  │
│   │  /             portal/dist/*    │  │
│   │  /api/v1/*  →  broker on :8080  │  │
│   └──────────────────────────────────┘  │
└──────────────────┬──────────────────────┘
                   │ HTTP :8080
                   ▼
┌─────────────────────────────────────────┐
│  Broker process(es) — uvicorn           │
│   • One or many; workers self-elect     │
│     via pg_try_advisory_lock (see       │
│     "Multi-broker" below).              │
│   • All brokers share one Postgres,     │
│     one OPENVDI_JWT_SECRET, one         │
│     OPENVDI_ENCRYPTION_KEY.             │
└──────────────────┬──────────────────────┘
                   │
        ┌──────────┴──────────┐
        ▼                     ▼
┌──────────────┐       ┌─────────────────────┐
│  PostgreSQL  │       │  Hypervisor Cluster │
│              │       │  (Proxmox VE :8006) │
└──────────────┘       └─────────────────────┘
```

The portal and broker are reachable from the user's browser at **one origin** — same scheme, host, and port. This is a hard requirement of the M4 auth design; see *Same-Origin Requirement* below.

## Same-Origin Requirement

M4 stores refresh tokens in `HttpOnly; Secure; SameSite=Strict` cookies (per the M4 planning seed, decision A8). The `SameSite=Strict` flag tells the browser to omit the cookie on any cross-origin request — including the `/api/v1/auth/refresh` call from the portal — unless the portal and broker share an origin. With shared origin, the cookie is sent automatically; without shared origin, the refresh endpoint receives no cookie and the user is logged out on every access-token expiry.

This is an architectural choice that trades production deployment flexibility for a stronger security posture: the refresh token is unreachable to JavaScript (HttpOnly), the cookie can't be sent in CSRF flows (SameSite=Strict), and the access token's short TTL bounds blast radius if the page is compromised.

There are two ways to satisfy "one origin":

### Option 1 — Broker serves the portal static files

Build the portal once, drop the resulting `portal/dist/` next to the broker, and have the broker mount it via FastAPI's `StaticFiles`. Single process, single TLS termination, single config to manage.

```
broker (uvicorn :8080)
  ├─ /api/v1/*  →  FastAPI routes
  └─ /          →  StaticFiles("portal/dist")
```

**Pros:** simplest topology; one process to deploy; no separate web server.
**Cons:** broker restart briefly drops the portal load path; FastAPI is not the most efficient static-file server (negligible at v0 scale).

### Option 2 — Reverse proxy serves portal, proxies /api/* to broker

A reverse proxy (Caddy, nginx, Traefik) terminates TLS at the edge, serves the portal static files directly, and proxies the `/api/*` path to the broker upstream.

```
caddy (:443)
  ├─ /         →  /var/lib/openvdi/portal/dist/
  └─ /api/*    →  http://broker:8080
```

**Pros:** separation of concerns; broker restarts don't drop the portal; the proxy can handle multiple brokers (load-balancing for HA); auto-HTTPS via Caddy.
**Cons:** another moving part to deploy and monitor.

**Recommended for v0:** **Option 2 with Caddy**, because Caddy ships with automatic HTTPS via Let's Encrypt and the config is a few lines.

Example `Caddyfile`:

```
openvdi.example.com {
    encode gzip

    handle /api/* {
        reverse_proxy broker_a:8080 broker_b:8080 {
            health_uri /health
        }
    }

    handle {
        root * /var/lib/openvdi/portal/dist
        try_files {path} /index.html
        file_server
    }
}
```

The `try_files {path} /index.html` line is what makes client-side routing work — a hit on `/admin/sessions` falls through to `index.html`, which loads the SPA, which routes via React Router.

## Environment Variables

The broker reads its configuration from environment variables (typed `BaseSettings` per M4-02). All values must be set before the broker starts; unset required variables cause a fail-fast at startup.

| Variable | Required in prod | Notes |
|---|---|---|
| `OPENVDI_AUTH_MODE` | yes | Must be `jwt` in production. `dev` is the M2/M3 header path; the broker REFUSES to start in `dev` unless this is set explicitly. |
| `OPENVDI_JWT_SECRET` | yes (jwt mode) | HS256 signing key. ≥32 bytes random. Generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Identical across all broker instances. |
| `OPENVDI_ENCRYPTION_KEY` | yes | Fernet key for encrypting `clusters.token_secret` at rest. Generate with `python -m broker.app.crypto generate-key` (M2). Identical across all broker instances. **Store separately from the database backup** — this key plus a DB dump are sufficient to extract Proxmox credentials. |
| `OPENVDI_LDAP_URL` | yes (jwt mode) | e.g. `ldaps://dc1.example.com:636`. Use `ldaps://` in production. |
| `OPENVDI_LDAP_BIND_DN` | yes (jwt mode) | Service-account DN for the broker's LDAP queries, e.g. `CN=openvdi-svc,OU=ServiceAccounts,DC=example,DC=com`. |
| `OPENVDI_LDAP_BIND_PASSWORD` | yes (jwt mode) | Service-account password. Rotate per your AD policy. |
| `OPENVDI_LDAP_USER_BASE` | yes (jwt mode) | DN under which user objects live, e.g. `OU=Users,DC=example,DC=com`. |
| `OPENVDI_LDAP_GROUP_BASE` | yes (jwt mode) | DN under which group objects live. |
| `OPENVDI_LDAP_USER_FILTER` | no | Default `(sAMAccountName={username})`. Override for non-AD LDAP. |
| `OPENVDI_LDAP_GROUP_FILTER` | no | Default `(member={user_dn})`. Override for non-AD LDAP. |
| `OPENVDI_LDAP_ADMIN_GROUP` | yes (jwt mode) | Group name (or DN) whose members are granted admin role. |
| `OPENVDI_PORTAL_ORIGIN` | yes | The single origin the portal is served from, e.g. `https://openvdi.example.com`. Used for cookie domain matching and CORS configuration. |
| `OPENVDI_LOG_FORMAT` | no | `text` (dev default) or `json` (prod recommended). |
| `OPENVDI_LOG_LEVEL` | no | `INFO` default. |
| `OPENVDI_AUDIT_RETENTION_DAYS` | no | `90` default. Tune per compliance requirements. |
| `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | yes | Standard Postgres connection. |

Document any tightened values (auth tokens, encryption keys) in your secrets manager (Vault, sops, AWS Secrets Manager, etc.). Do not check `.env` into source control.

## TLS / HTTPS

The `Secure` flag on the refresh-token cookie tells the browser to send it only over HTTPS. **Production deployments MUST terminate TLS.** Plain HTTP works in dev (the broker omits the `Secure` flag when `OPENVDI_AUTH_MODE=dev`), but a production deployment over HTTP would silently break the auth flow on the first refresh.

In Option 2 (Caddy/nginx), the reverse proxy terminates TLS and the broker speaks plain HTTP on the internal network. In Option 1 (broker serves static), the broker either terminates TLS itself (uvicorn `--ssl-certfile` / `--ssl-keyfile`) or runs behind a TLS-terminating proxy anyway (in which case you might as well move to Option 2).

Cert renewal: with Caddy + Let's Encrypt, automatic. With nginx + manual certs, set up `certbot` cron.

In addition, the noVNC console connection (M3 portal) goes browser-direct to the Proxmox node over `wss://`. The PVE self-signed certificate must be trusted by every user's browser — for v0 LAN deployments, that's a one-time browser prompt per user. Production deployments should issue a real cert to PVE and configure PVE to use it; see Proxmox's `pve-firewall` / `pveproxy` documentation.

## Database Migrations

OpenVDI uses Alembic (M4-01). Migrations apply forward-only via:

```bash
cd broker
alembic upgrade head
```

Run this on every deploy. Alembic is idempotent — applying a migration that's already applied is a no-op.

For schema changes that involve column drops, table renames, or anything irreversibly destructive, the recommended posture is **blue-green deployment with downtime tolerance** for v0: stop the broker, snapshot the DB, run the migration, start the new broker. Zero-downtime migrations are an M5+ concern (paired with the broker rolling-restart pattern).

`db/drop_all.sql` exists for nuclear development resets and should not be present on any production system.

## Multi-Broker

OpenVDI v0 supports **multiple broker instances against one shared Postgres**. The HTTP API surface is stateless, so a reverse proxy load-balancer in front of N brokers handles request distribution naturally — sticky sessions are NOT required.

The four background workers (`session_monitor`, `pool_provisioner`, `task_tracker`, `health_checker`) plus the `audit_retention` worker self-elect a leader using Postgres advisory locks. Each broker process attempts to acquire `pg_try_advisory_lock(hashtext('openvdi-worker:<name>'))` on a dedicated connection at startup. The acquiring broker becomes the leader for that worker; non-leaders sleep 30s and retry. If the leader dies (process crash, pod kill, network partition), its connection drops, the lock auto-releases, and a follower picks it up on its next retry.

This means at any instant, exactly one broker runs each worker — even with N brokers up. Different workers may land on different brokers, naturally distributing the worker load.

### Cluster credential propagation

When admin runs `PUT /clusters/{id}` on broker A, broker A swaps its cached provider instance immediately. **Broker B is unaware until its `health_checker` worker's next tick (~60s).** Each broker's `health_checker` compares each cluster's `updated_at` against the timestamp the broker last constructed its provider; if the DB is newer, it tears down the old provider and constructs a new one with current credentials.

**Operational consequence:** in the ~60s window after a credential change, broker B may continue using stale credentials and surface `ProviderAuthError` on requests routed to it. The portal's BrokerError UI handles this gracefully (the user sees a transient error and can retry), but for production it's worth documenting in your runbook: "After changing cluster credentials, expect up to ~60s of mixed-state."

If your deployment can't tolerate that window, the admin can roll-restart all brokers after the credential change; each comes up clean against the new credentials. M5+ may add a faster cross-broker sync mechanism (Postgres `LISTEN`/`NOTIFY` is the natural fit).

### What's safe to run multi-broker

- HTTP API surface (stateless).
- JWT validation (every broker has the same signing key; validation is local).
- Refresh tokens (DB-backed; revocations visible to all brokers immediately).
- LDAP queries (every broker queries directly).
- Audit logging (DB-backed).

### What requires the leader election

- Workers (`session_monitor`, `pool_provisioner`, `task_tracker`, `health_checker`, `audit_retention`).
- Cluster ping at startup (already a one-shot per-broker; the worker takes over from t=60s).

### What's NOT supported

- Sticky sessions are not required and not supported. Don't configure your load balancer for them.
- Per-broker state (in-memory caches, etc.) — there is none. If a future feature requires it, it must be either (a) DB-backed or (b) safe to be inconsistent across brokers.

## Logging

Production deployments should set `OPENVDI_LOG_FORMAT=json`. The broker emits one JSON object per log record to stdout; pipe to your aggregator (Loki, Splunk, ELK, Datadog, whatever). Required fields:

```json
{
  "timestamp": "2026-04-27T12:34:56.789Z",
  "level": "INFO",
  "logger": "broker.app.workers.session_monitor",
  "message": "logoff detected, refreshing desktop",
  "worker": "session_monitor",
  "desktop_id": "...",
  "vmid": 5003
}
```

Request-handling logs carry `request_id` (correlates entries from one HTTP request); worker logs carry `worker` (correlates entries from one worker tick). The middleware sets `X-Request-ID` on every response so frontend errors can be correlated to backend logs.

## MCP Server Deployment

The `openvdi-admin` MCP server runs on the agent's host, NOT alongside
the broker. Common deployments:

- **Operator's laptop** running Claude Desktop or Claude Code, MCP
  spawned as a subprocess by the agent.
- **Praxova IT Agent platform** instance, MCP hosted as a tool server.
- **Headless CI runner** for OpenVDI scenario testing, MCP spawned by
  test-runner Python.

The MCP is stateless and pip-installable from the OpenVDI monorepo.
Multiple MCP processes against the same broker are fine — they each
hold an independent service-account session.

### Service account creation

Create a regular AD user (e.g. `openvdi-mcp-svc`) and add it to the
group named in `OPENVDI_LDAP_ADMIN_GROUP`. The MCP authenticates exactly
the way an admin user does from the portal — no new identity concept,
no broker-side configuration.

Per-agent attribution: if you want different agent products to be
distinguishable in the broker's audit log, create separate AD service
accounts per product (e.g. `openvdi-itagent-svc`, `openvdi-installer-svc`).
The audit log's `actor` field will show which one acted.

Rotate the password per your AD policy. Restart the MCP after rotation
(no SIGHUP-driven config reload in v0; that's M6+).

### Installation

```bash
git clone git@github.com:Praxova/OpenVDI.git
cd OpenVDI/mcp/openvdi-admin
pip install -e ".[dev]"
```

Python 3.10+ required (matches the broker's dev environment). The `mcp[cli]` SDK install can be slow on constrained hosts; if `pip install -e ".[dev]"` stalls, fall back to:

```bash
pip install --no-deps -e .
pip install pytest pytest-asyncio respx ruff mypy  # dev deps
pip install "mcp[cli]>=1.0.0"  # the actual SDK; let it complete in background
```

Verify with:

```bash
python -m openvdi_admin.server
# Hangs waiting for MCP-protocol input on stdin. Ctrl-C to exit.
```

### Environment variables

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `OPENVDI_BROKER_URL` | yes | — | Same origin the portal uses, e.g. `https://openvdi.example.com` |
| `OPENVDI_SERVICE_USER` | yes | — | AD username of the MCP service account |
| `OPENVDI_SERVICE_PASSWORD` | yes | — | Service-account password (loaded as SecretStr; never logged) |
| `OPENVDI_VERIFY_SSL` | no | `true` | Set `false` only for self-signed dev clusters |
| `OPENVDI_MCP_READ_ONLY` | no | `false` | When `true`, every destructive tool refuses to execute |
| `OPENVDI_MCP_LOG_FORMAT` | no | `text` | `text` (dev) or `json` (production) |
| `OPENVDI_MCP_LOG_LEVEL` | no | `INFO` | Standard Python log level |
| `OPENVDI_MCP_LOG_TOOL_STARTS` | no | `false` | If `true`, logs both tool start AND completion (2× volume) |

The MCP refuses to start if any required variable is unset. Loading is fast-fail at module import.

### Configuring the agent client

For Claude Desktop, add the MCP to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "openvdi-admin": {
      "command": "openvdi-admin",
      "env": {
        "OPENVDI_BROKER_URL": "https://openvdi.example.com",
        "OPENVDI_SERVICE_USER": "openvdi-mcp-svc",
        "OPENVDI_SERVICE_PASSWORD": "...",
        "OPENVDI_MCP_LOG_FORMAT": "json"
      }
    }
  }
}
```

For Claude Code, configure in `.claude/config.toml`:

```toml
[mcp.openvdi-admin]
command = "openvdi-admin"

[mcp.openvdi-admin.env]
OPENVDI_BROKER_URL = "https://openvdi.example.com"
OPENVDI_SERVICE_USER = "openvdi-mcp-svc"
OPENVDI_SERVICE_PASSWORD = "..."
```

For the Praxova IT Agent platform, see the IT Agent's own deployment docs — the OpenVDI MCP integrates as a tool server using the same env vars.

### Logging and correlation

The MCP emits one log line per tool completion to stderr (stdout is reserved for the MCP protocol). With `OPENVDI_MCP_LOG_FORMAT=json`, each line is a structured JSON record:

```json
{
  "timestamp": "2026-04-30T14:22:18.450Z",
  "level": "INFO",
  "logger": "openvdi_admin._tool_wrapper",
  "message": "tool completed",
  "tool": "openvdi_create_pool",
  "request_id": "9f1a2c3d-...",
  "outcome": "ok",
  "duration_ms": 82,
  "result_envelope_ok": null
}
```

The MCP propagates `request_id` to the broker via the `X-Request-ID` header. The broker's M4-12 logging (when `OPENVDI_LOG_FORMAT=json`) includes `request_id` on every line. To trace a problem end-to-end:

1. Find the MCP log line for the failing tool call. Note `request_id`.
2. `grep <request_id> /var/log/openvdi/broker.log` — finds every broker-side line for that request.
3. `psql -c "SELECT * FROM audit_log WHERE details->>'request_id' = '<rid>';"` — finds the audit row for the action.

Tool args are NOT logged on the MCP side. They appear in the broker's audit log with sensitive fields redacted per M2-12.

### Read-only mode

For diagnostic-only deployments (e.g. an IT Agent that should only investigate, never mutate), set `OPENVDI_MCP_READ_ONLY=true`. Every destructive tool — every `create`, `update`, `delete`, `power`, `provision`, `drain`, `rebuild`, `assign`, `unassign`, `force_disconnect`, `grant`, `revoke`, plus the destructive intent tools — returns a structured `READ_ONLY_MODE` error instead of executing.

Granular per-tool tiers (e.g. "diagnostic + power, no delete") are M6+; v0 is binary.

### Audit attribution

Every action the MCP triggers shows up in the broker's `audit_log` attributed to the service-account username. If a customer wants to distinguish "Bob via the agent" from "the agent acting on its own schedule," that's a policy concern for the agent's own logs, not the MCP's. The audit row carries `request_id` to correlate with the agent's session.

## Backup and Recovery

**Postgres dump nightly minimum.** OpenVDI's database is the source of truth for everything except live VM state. A clean restore + broker restart is a complete recovery — the brokers reconstruct provider instances from the `clusters` table on startup and the workers rebuild operational state from there.

Two values must be backed up **separately from the DB dump**:
- `OPENVDI_ENCRYPTION_KEY` — protects `clusters.token_secret` (Proxmox credentials) at rest. A DB dump alone is useless without it.
- `OPENVDI_JWT_SECRET` — without it, all in-flight access tokens are invalidated (users re-login). Less critical operationally but document the dependency.

Recovery procedure:
1. Restore Postgres from the most recent dump.
2. Run `alembic upgrade head` to ensure the schema matches the current broker code.
3. Restore the `OPENVDI_ENCRYPTION_KEY` and `OPENVDI_JWT_SECRET` from the secrets manager.
4. Start the broker(s).
5. Validate via `GET /health` and a `POST /auth/login` round-trip.

VMs themselves are not backed up by OpenVDI — that's the hypervisor's concern. The Proxmox provider's `destroy_vm` is the only OpenVDI-driven destructive operation; admin DELETE flows are auditable in `audit_log`.

**MCP servers have no state.** No backup concern. The MCP's only
configuration is environment variables (handled by your secrets
manager) and the OpenVDI monorepo (handled by source control). A clean
restart of an MCP process is fully self-recovering — it logs back in
on the first tool call.

## Health and Monitoring

`GET /health` returns 200 OK on broker liveness. It does not assert worker state, DB reachability, or cluster reachability — those are the job of M5+'s readiness endpoint.

For now, monitor:
- HTTP 5xx rate on the broker (any reverse-proxy access log).
- Postgres connection count (each broker holds ~5 dedicated worker connections plus a request-scoped pool).
- `cluster.status = 'offline'` rows (admin dashboard, or scrape via `GET /api/v1/clusters` against an admin token).
- Broker stdout for ERROR-level lines (the worker resilience pattern logs but doesn't crash on tick errors).

M5+ adds Prometheus metrics, an OpenTelemetry tracing surface, and a structured `/ready` endpoint. Until then, ops runs on logs and dashboards.

## Out of Scope for v0

Documented limitations operators should know about up front:

- **Auto-scaling.** Static N-broker deployment. Adding a broker is a config change + restart, not automatic.
- **Distributed tracing.** No OpenTelemetry surface in v0.
- **Prometheus metrics.** Logs only.
- **Blue/green deployment automation.** Manual cutover; downtime acceptable for schema migrations.
- **Service mesh.** Out of scope; the broker → Postgres and broker → Proxmox connections are direct.
- **Per-broker rate limiting.** No protection against runaway clients in v0; trust the LAN. M5+ when there's a public deployment.
- **Geographic redundancy.** Single-region deployment. Multi-region (with a leader-election quorum that survives a region failure) is post-v0.
- **WAN-facing console (noVNC over the public internet).** v0 is LAN-only; KasmVNC + a reverse proxy land in v1.

When any of these become live concerns, revisit. Until then, document the gap in the operator runbook and keep moving.
