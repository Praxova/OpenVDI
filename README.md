# OpenVDI

**Open-source, hypervisor-agnostic VDI.** Self-hosted, AI-driven, and built for
organizations unhappy with the constant price increases of other providers.

OpenVDI delivers Windows or Linux desktops to a browser via Proxmox VE today,
with vSphere, Hyper-V, and XCP-ng on the roadmap. Every layer — broker,
portal, console, management — is yours to deploy and modify.

---

## Why OpenVDI

Horizon ran on a VMware-shaped world. After Broadcom, that world is changing.
Customers who built around `vSphere + Horizon` are looking for a path that
isn't "rewrite to a different proprietary stack." OpenVDI is that path:

- **Free your hypervisor.** Proxmox today, vSphere/Hyper-V/XCP-ng tomorrow.
  The provider abstraction is in the codebase from day 1, not a future
  retrofit. Adding a provider is a contained code change in
  `broker/app/providers/`, not a fork.
- **Run it yourself.** Apache 2.0 licensed. No connection servers phoning
  home, no per-CCU fees, no telemetry.
- **Manage with AI agents.** OpenVDI ships an MCP server (43 tools, 6
  intent-level orchestrations) so Claude Desktop, Claude Code, or
  Praxova's IT Agent can drive your deployment in plain English.
  See [`docs/mcp.md`](docs/mcp.md).

---

## What's in the box

| Component | What it does |
|---|---|
| **Broker** | FastAPI service. Pool management, entitlement enforcement, session lifecycle, console ticket issuance. PostgreSQL-backed. |
| **Portal** | React + Vite SPA. User desktop launcher, browser console (noVNC v0; KasmVNC v1), admin dashboard with cluster/template/pool/desktop/session/audit CRUD. |
| **MCP server** | `openvdi-admin`. Exposes the full admin surface to AI agents. 37 thin-wrapper tools (one per endpoint) + 6 intent tools (`smoke_test`, `deploy_pool`, `diagnose_user`, etc.). |
| **Workers** | In-broker async tasks: pool provisioner, session monitor, task tracker, health checker, audit retention. Multi-broker safe via Postgres advisory locks. |

```
                         Browsers / AI Agents
                                  │
                           HTTPS + MCP/stdio
                                  ▼
                ┌──────────────────────────────┐
                │   Reverse proxy (Caddy)      │
                │      portal/dist + /api/*    │
                └──────────────┬───────────────┘
                               │
                ┌──────────────▼───────────────┐
                │   OpenVDI broker (FastAPI)   │
                │   + 5 background workers     │
                └────────┬─────────────┬───────┘
                         │             │
                  ┌──────▼────┐   ┌────▼──────────────┐
                  │ Postgres  │   │ Hypervisor (PVE)  │
                  └───────────┘   └───────────────────┘
```

---

## Status

**v0.5.0 — beta-ready.** Tagged at [`m5-complete`](https://github.com/Praxova/OpenVDI/releases/tag/m5-complete).

What's shipped:

- ✅ Broker with full admin API (clusters, templates, pools, desktops, sessions, entitlements, audit, dashboard, user diagnostics)
- ✅ React portal with noVNC console, admin dashboard, role-gated routes
- ✅ JWT + LDAP/AD authentication, refresh-cookie session management
- ✅ Five background workers (pool provisioner, session monitor, task tracker, health checker, audit retention)
- ✅ Proxmox provider with `HypervisorProvider` Protocol — second-provider-ready
- ✅ MCP server (43 tools)
- ✅ Apache 2.0 licensing across the stack
- ✅ Multi-broker HA topology (advisory-lock leader election)

Roadmap (post-beta):

- 🔜 KasmVNC v1 for WAN-quality display (WebRTC + GPU encode)
- 🔜 Second hypervisor provider (vSphere or XCP-ng, when validation partner materializes)
- 🔜 `openvdi-installer` MCP for new-customer bootstrap from bare Proxmox
- 🔜 Pass-through user JWTs in the MCP layer

---

## Get started

The full step-by-step installation walkthrough is **[`installation.md`](installation.md)** — 16 stages, ~2–4 hours from "I have a Proxmox cluster" to "users are connecting to desktops in a browser."

Quick orientation by role:

| You're a... | Start here |
|---|---|
| Sysadmin deploying OpenVDI | **[`installation.md`](installation.md)** |
| Operator running a deployed instance | [`docs/deploy.md`](docs/deploy.md) — TLS, multi-broker HA, backup, monitoring |
| Developer extending the broker / portal / MCP | [`docs/architecture.md`](docs/architecture.md), [`broker/README.md`](broker/README.md), [`portal/README.md`](portal/README.md), [`mcp/openvdi-admin/README.md`](mcp/openvdi-admin/README.md) |
| AI agent operator (Claude Desktop / IT Agent) | [`docs/mcp.md`](docs/mcp.md) |
| Curious about the design | [`docs/architecture.md`](docs/architecture.md) — the *why* lives here |

### Try it locally

You'll still want to read the install guide — but the dev-mode quickstart is:

```bash
# Postgres + pgAdmin
docker compose up -d

# Broker
cd broker
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --port 8080

# Portal (separate shell)
cd portal
pnpm install
pnpm dev
```

You'll need to fill in `.env` per [`installation.md`](installation.md) — Proxmox credentials, AD/LDAP config, and the broker secrets (encryption key + JWT secret).

---

## The AI angle

OpenVDI is the first VDI platform built around an AI-agent management
layer. Every admin action — registering a cluster, deploying a pool, force-
disconnecting a stuck session, diagnosing why a user can't connect — is
exposed as a typed MCP tool that Claude or any other agent can drive.

```python
# Pseudocode from the agent's perspective
result = openvdi_diagnose_user("alice")
# →  ok=True, summary="entitled but blocked: POOL_FULL",
#    directly_entitled_pools=[{...}], potential_group_entitlements=[...]
```

Praxova ships the [IT Agent platform](https://praxova.ai) as the
hosted-agent path; the MCP itself stays free under Apache 2.0, so customers
running Claude Desktop (or any other MCP-compatible agent) get the same
tooling at no charge.

---

## Documentation

| Doc | Purpose |
|---|---|
| [`installation.md`](installation.md) | Manual deployment walkthrough (the one-stop guide) |
| [`docs/architecture.md`](docs/architecture.md) | System design, layering, MCP surface, technical risks |
| [`docs/deploy.md`](docs/deploy.md) | Production decisions: TLS, multi-broker, backup, monitoring |
| [`docs/api-design.md`](docs/api-design.md) | REST API surface |
| [`docs/database-schema.md`](docs/database-schema.md) | Data model |
| [`docs/providers.md`](docs/providers.md) | The `HypervisorProvider` interface (for new-provider authors) |
| [`docs/providers/proxmox.md`](docs/providers/proxmox.md) | Proxmox provider implementation + operational quirks |
| [`docs/session-tracking.md`](docs/session-tracking.md) | Session lifecycle, guest-agent polling |
| [`docs/mcp.md`](docs/mcp.md) | MCP server: tool catalog, troubleshooting |
| [`docs/implementation-plan.md`](docs/implementation-plan.md) | Milestone log + key design decisions |

---

## License

[Apache License 2.0](LICENSE). Same license used across Praxova's open
components, including the IT Agent.

The broker, portal, and MCP are free to use, modify, and redistribute. There
is no Praxova "Enterprise Edition" of OpenVDI — the open-source version is
the only version. Praxova's commercial offering is the IT Agent platform
above the MCP, not a closed fork below it.

---

## Contributing

OpenVDI is in active development. Issues and discussion welcome at
[github.com/Praxova/OpenVDI/issues](https://github.com/Praxova/OpenVDI/issues).

For substantive contributions (new providers, new MCP tools, broker
features), please open an issue first to align on direction. The project's
opinion-density is high in some places — we'd rather agree on the shape
before code lands.

---

## About Praxova

[Praxova](https://praxova.ai) builds AI-agent infrastructure for IT teams.
OpenVDI is one of several open-source platforms Praxova maintains, alongside
the proprietary IT Agent platform that consumes them. The pattern is
deliberate: keep the foundation open, let customers run it themselves, and
charge for the agent layer that makes operating it effortless.
