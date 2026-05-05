# OpenVDI Installation Guide

A step-by-step walkthrough for setting up an OpenVDI instance from
scratch. Follow it top to bottom; each stage finishes with a
verification step.

This guide is opinionated — it tells you what to do, not all the
ways you could do it. For the production-decision reference (why
same-origin, why these env vars, multi-broker HA topology, etc.)
see `docs/deploy.md`. For the system overview and design, see
`docs/architecture.md`.

## Audience and scope

You'll get the most out of this guide if you:

- Have a working **Proxmox VE 9.x** cluster and root access to its
  nodes.
- Have an **Active Directory (or generic LDAP)** directory you can
  create groups and service accounts in.
- Are comfortable on a Linux host as root, with basic Postgres,
  systemd, and TLS.

Total time budget for an experienced sysadmin: **2–4 hours** for
stages 1–10 (broker + portal up, first admin login). Add another
hour for the first cluster/template/pool registration and the
end-user logon smoke test.

The guide produces a single-broker, single-host deployment with
TLS, the React portal, and the MCP server. Multi-broker HA, the
backup/restore drill, and operational tuning live in `docs/deploy.md`.

## Architecture overview

```
                        Browsers
                            │
                            ▼ HTTPS :443
              ┌──────────────────────────────┐
              │  Reverse proxy (Caddy)       │
              │   /         → portal/dist    │
              │   /api/*    → broker:8080    │
              └──────────────┬───────────────┘
                             │ HTTP :8080
                             ▼
                  ┌────────────────────────┐
                  │  OpenVDI broker        │
                  │  (FastAPI + uvicorn)   │
                  │  + 5 background        │
                  │  workers               │
                  └──────┬──────────┬──────┘
                         │          │
                ┌────────▼────┐  ┌──▼─────────────┐
                │ PostgreSQL  │  │ Proxmox VE     │
                │ (same host  │  │ (cluster nodes,│
                │  is fine)   │  │  remote)       │
                └─────────────┘  └────────────────┘
                         ▲
                         │ LDAPS :636
                ┌────────┴────────┐
                │  AD / LDAP      │
                └─────────────────┘
```

The broker, portal, and Postgres can all live on one Linux host
for v0. PVE nodes and the AD server are remote. The MCP server
(optional) runs anywhere — typically on the agent operator's host.

## Prerequisites checklist

Print this and tick before starting.

### Hypervisor (Proxmox VE)

- [ ] Proxmox VE **9.x** cluster reachable on the network. Single-node
  is fine.
- [ ] At least one **storage** with thin-provisioning enabled (for
  linked clones). `local-lvm` works out of the box.
- [ ] An **unused VMID range** (e.g. 5000–5099) reserved for OpenVDI
  desktops on each cluster you'll register.
- [ ] **Root SSH** access to each PVE node (needed once for the LVM
  lock-cleanup unit).
- [ ] A **base VM template** to clone from — see Stage 4.

### Identity (AD or LDAP)

- [ ] **AD/LDAP** server reachable over **LDAPS** (port 636). Plain
  LDAP works for testing but is not recommended.
- [ ] Permissions to create groups and service accounts.
- [ ] The AD CA chain available, or you accept dev-mode
  `OPENVDI_LDAP_VERIFY_SSL=false` until you sort certs.

### OpenVDI host

- [ ] One Linux server (Debian 12 / Ubuntu 24.04 LTS recommended)
  with **2 vCPU / 4 GB RAM / 20 GB disk** minimum.
- [ ] Sudo / root access.
- [ ] A **DNS name** that will resolve to this host
  (e.g. `openvdi.example.com`) — required for the same-origin TLS
  setup.
- [ ] **Outbound HTTPS** to your PVE nodes' API port (8006) and to
  the AD/LDAP server.
- [ ] **Inbound HTTPS** (443) from the user network.
- [ ] **A way to obtain a TLS certificate** for `openvdi.example.com`
  (Let's Encrypt is the easy path; corporate CAs work too).

### Software the host needs

You'll install these in Stage 5:

- PostgreSQL 16+
- Python 3.12+
- Node.js 20+ and pnpm
- Caddy (or nginx — Caddy is the default in this guide because
  auto-HTTPS keeps the cert story simple)
- git

---

## Stage 1 — Plan the deployment

Before touching anything, write down:

| What | Example | Yours |
|---|---|---|
| Public DNS name | `openvdi.example.com` |  |
| Broker host LAN IP | `10.0.0.50` |  |
| PVE cluster API URL | `https://10.0.0.2:8006` |  |
| PVE cluster name (in OpenVDI) | `dev-cluster` |  |
| PVE service account | `openvdi@pve` |  |
| AD/LDAP URL | `ldaps://dc1.example.com:636` |  |
| AD admin group | `OpenVDI-Admins` |  |
| AD broker bind account | `openvdi-svc@example.com` |  |
| OpenVDI desktop VMID range | `5000–5099` |  |
| OpenVDI VM name prefix | `OPENVDI-` |  |

These values appear repeatedly. Get them right once.

**Same-origin requirement (important).** OpenVDI's auth design uses
`SameSite=Strict` refresh-token cookies, which means the portal and
the broker MUST be served from the *same* DNS name and port. The
deployment in this guide does this by putting Caddy in front of
both. See `docs/deploy.md` → *Same-Origin Requirement* for the full
explanation. Don't try to host the portal at `portal.example.com`
and the broker at `api.example.com` — auth will silently break.

---

## Stage 2 — Prepare Proxmox

### 2.1 Create the OpenVDI service account + API token

On any PVE node, as root:

```bash
# Create a dedicated user in the PVE realm
pveum useradd openvdi@pve --comment "OpenVDI broker"

# Define a role with only what OpenVDI needs (least-privilege)
pveum role add OpenVDIBroker -privs \
  "VM.Clone,VM.Allocate,VM.Config.CPU,VM.Config.Memory,VM.Config.Disk,\
VM.Config.Network,VM.Config.Options,VM.Config.HWType,VM.Config.Cloudinit,\
VM.PowerMgmt,VM.Snapshot,VM.Snapshot.Rollback,VM.Console,VM.Monitor,VM.Audit,\
Datastore.AllocateSpace,Datastore.Audit,Sys.Audit,SDN.Use"

# Bind the role to the PVE root path for the user
pveum acl modify / -user openvdi@pve -role OpenVDIBroker

# Create an API token for the user; token is single-use-show, save the secret now
pveum user token add openvdi@pve openvdi --privsep=0
```

The token output looks like:

```
┌──────────────┬──────────────────────────────────────┐
│ key          │ value                                │
├──────────────┼──────────────────────────────────────┤
│ full-tokenid │ openvdi@pve!openvdi                  │
│ value        │ 9f1a2c3d-e5f6-7890-abcd-ef1234567890 │
└──────────────┴──────────────────────────────────────┘
```

**Save the `value` (the secret UUID) — Proxmox will not show it
again.** You'll paste it into OpenVDI's cluster registration in
Stage 11.

The full privilege list and rationale is in
`docs/providers/proxmox.md` → *Service Account Setup*.

### 2.2 Verify the token works

From the OpenVDI host (or any machine that can reach PVE):

```bash
curl -sk -H "Authorization: PVEAPIToken=openvdi@pve!openvdi=<SECRET>" \
  https://10.0.0.2:8006/api2/json/nodes | jq .
```

Expected: a JSON list of nodes. If you see `401 authentication failure`,
re-check the `Authorization` header format (it's a single line with
`!` and `=` between fields, not separators).

### 2.3 Install the LVM-lock-cleanup systemd unit

On every PVE node, as root. Unclean shutdowns can leave stale
`/run/lock/lvm/P_*` files that cause every subsequent clone to fail
with `can't lock file`. This unit clears them at boot.

```bash
cat > /etc/systemd/system/lvm-lock-cleanup.service <<'EOF'
[Unit]
Description=Clear orphaned LVM locks left by unclean shutdowns
DefaultDependencies=no
Before=local-fs.target lvm2-monitor.service
After=systemd-tmpfiles-setup.service
ConditionPathExistsGlob=/run/lock/lvm/P_* /run/lock/lvm/V_*

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'rm -f /run/lock/lvm/P_* /run/lock/lvm/V_* || true'

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable lvm-lock-cleanup.service
```

It runs at next boot. To clear locks now without rebooting:

```bash
rm -f /run/lock/lvm/P_* /run/lock/lvm/V_*
systemctl restart lvm2-monitor
```

Background and rationale: `docs/providers/proxmox.md` → *LVM Lock
Cleanup*.

---

## Stage 3 — Prepare AD/LDAP

You need three things in your directory:

### 3.1 Admin group

Create a security group named **`OpenVDI-Admins`** (or whatever you
want — you'll set `OPENVDI_LDAP_ADMIN_GROUP` to match). Members of
this group will be able to log in to the OpenVDI admin dashboard.

Add at least your own user to this group so you can log in.

### 3.2 Broker bind account

The broker uses an LDAP service account to look up users at login
time. It binds as this account, finds the user's DN, then re-binds
as the user to verify their password.

Create a regular AD user, e.g. `openvdi-svc`. It does NOT need to
be in the admin group — it just needs to be able to read user and
group objects in the search bases you'll configure.

Set a strong password and don't expire it (or rotate it on a
schedule and restart the broker after each rotation).

### 3.3 (Optional) MCP service account

If you plan to drive OpenVDI from AI agents (Claude Desktop, Praxova
IT Agent, etc.) via the MCP server, create a SECOND AD user (e.g.
`openvdi-mcp-svc`) and add it to `OpenVDI-Admins`. See `docs/mcp.md`
for the rationale.

### 3.4 Verify the bind account

From the OpenVDI host, after installing `ldap-utils`:

```bash
sudo apt install -y ldap-utils

ldapsearch -x \
  -H ldaps://dc1.example.com:636 \
  -D "CN=openvdi-svc,OU=ServiceAccounts,DC=example,DC=com" \
  -w '<bind-password>' \
  -b "OU=Users,DC=example,DC=com" \
  "(sAMAccountName=<your-username>)" \
  cn sAMAccountName memberOf
```

You should see your user record. If `ldapsearch` errors with
`Can't contact LDAP server`, check firewall rules and DNS. If it
errors with `Invalid credentials`, the bind DN or password is
wrong.

---

## Stage 4 — Build the base desktop VM template

Out of every step, this one varies most by environment. The short
version: you need a Proxmox VM that's been turned into a **template**
(via PVE's "Convert to template" action) and has the **QEMU guest
agent** installed and running.

### Option A — Use the Packer template (Windows 11)

The repo ships a Packer config under `infra/packer/openvdi-win11-template/`.
If you have Windows 11 ISO + a PVE node + Packer installed, this is
the fastest path. Read `infra/packer/openvdi-win11-template/README.md`
for the prerequisites and run.

### Option B — Build manually

1. In the PVE UI: **Create VM**.
   - Use VirtIO SCSI controller, VirtIO network card.
   - Memory: 4 GB (you can change later per pool).
   - Disk: 60 GB on local-lvm or your thin-provisioning storage.
2. Mount your OS ISO and install the OS normally.
3. **Install the QEMU guest agent** in the guest:
   - Windows: install `virtio-win-guest-tools` from the
     `virtio-win.iso` (Red Hat ships it).
   - Linux: `apt install qemu-guest-agent && systemctl enable
     --now qemu-guest-agent`.
4. In the PVE UI for the VM: **Options → QEMU Guest Agent: Yes**.
5. Stop the VM. PVE UI: **More → Convert to template**.
6. Note the VMID (you'll register this template in Stage 12).

The template MUST have a working guest agent — OpenVDI's session
tracker polls it. A template without one will provision desktops
that fail to report ready. See `docs/session-tracking.md` for what
the agent is used for.

---

## Stage 5 — Set up the OpenVDI host

From here on, all commands are on the OpenVDI Linux host (Debian /
Ubuntu).

### 5.1 Install system packages

```bash
sudo apt update
sudo apt install -y \
  postgresql postgresql-contrib \
  python3.12 python3.12-venv python3-pip \
  curl git \
  caddy \
  nodejs npm
sudo npm install -g pnpm
```

(If your distro doesn't ship Python 3.12, add the deadsnakes PPA on
Ubuntu, or use pyenv. The broker requires 3.12+.)

### 5.2 Create the openvdi user

```bash
sudo useradd --system --home /opt/openvdi --shell /bin/bash --create-home openvdi
sudo mkdir -p /opt/openvdi /var/lib/openvdi /var/log/openvdi
sudo chown -R openvdi:openvdi /opt/openvdi /var/lib/openvdi /var/log/openvdi
```

### 5.3 Clone the repo

```bash
sudo -u openvdi -i
git clone https://github.com/Praxova/OpenVDI.git /opt/openvdi/OpenVDI
cd /opt/openvdi/OpenVDI
exit  # back to your normal user
```

---

## Stage 6 — Set up Postgres

### 6.1 Create the database and user

```bash
sudo -u postgres psql <<'SQL'
CREATE USER openvdi WITH PASSWORD 'openvdi';   -- pick a real password
CREATE DATABASE openvdi OWNER openvdi;
\connect openvdi
GRANT ALL ON SCHEMA public TO openvdi;
SQL
```

### 6.2 Run the schema migrations

```bash
sudo -u openvdi -i
cd /opt/openvdi/OpenVDI/broker
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

The broker reads its DB connection string from `.env` (which doesn't
exist yet). Create a temporary one for migration:

```bash
cat > /opt/openvdi/OpenVDI/.env.migrate <<'EOF'
POSTGRES_USER=openvdi
POSTGRES_PASSWORD=openvdi
POSTGRES_DB=openvdi
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
EOF

# Alembic reads broker/alembic.ini which uses the env vars above.
cd /opt/openvdi/OpenVDI/broker
ENV_FILE=/opt/openvdi/OpenVDI/.env.migrate alembic upgrade head
```

### 6.3 Verify

```bash
psql "postgresql://openvdi:openvdi@localhost/openvdi" -c '\dt'
```

You should see ~10 tables (`clusters`, `templates`, `pools`,
`desktops`, `sessions`, `entitlements`, `audit_log`, etc.) plus
`alembic_version`.

The raw-SQL files under `db/` are historical; Alembic is the
canonical migration path. See `broker/README.md` → *Database
migrations*.

---

## Stage 7 — Configure and install the broker

### 7.1 Generate secrets

The broker needs three secrets:

```bash
cd /opt/openvdi/OpenVDI/broker
source .venv/bin/activate

# Encryption key (Fernet, for clusters.token_secret at rest)
ENC_KEY=$(python -m app.crypto generate-key)

# JWT signing secret (≥32 bytes)
JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(48))')

echo "ENC_KEY=$ENC_KEY"
echo "JWT_SECRET=$JWT_SECRET"
```

**Save both into your secrets manager NOW.** Losing the encryption
key means losing all stored cluster credentials (you'll have to
re-enter them after a restore). Losing the JWT secret invalidates
in-flight access tokens (less critical — users just re-login).

### 7.2 Write the real `.env`

Replace `/opt/openvdi/OpenVDI/.env.migrate` (the migration-only file)
with the production `.env`:

```bash
cat > /opt/openvdi/OpenVDI/.env <<EOF
# Postgres
POSTGRES_USER=openvdi
POSTGRES_PASSWORD=openvdi
POSTGRES_DB=openvdi
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

# Encryption key for clusters.token_secret (NEVER commit, NEVER lose)
OPENVDI_ENCRYPTION_KEY=${ENC_KEY}

# Auth mode + JWT
OPENVDI_AUTH_MODE=jwt
OPENVDI_JWT_SECRET=${JWT_SECRET}

# LDAP / AD
OPENVDI_LDAP_URL=ldaps://dc1.example.com:636
OPENVDI_LDAP_BIND_DN=CN=openvdi-svc,OU=ServiceAccounts,DC=example,DC=com
OPENVDI_LDAP_BIND_PASSWORD=<your-bind-password>
OPENVDI_LDAP_USER_BASE=OU=Users,DC=example,DC=com
OPENVDI_LDAP_GROUP_BASE=OU=Groups,DC=example,DC=com
OPENVDI_LDAP_ADMIN_GROUP=OpenVDI-Admins
OPENVDI_LDAP_VERIFY_SSL=true

# Portal origin (must match the public DNS the user types)
OPENVDI_PORTAL_ORIGIN=https://openvdi.example.com

# Logging — text in dev, json in prod
OPENVDI_LOG_FORMAT=json
OPENVDI_LOG_LEVEL=INFO

# Audit retention
OPENVDI_AUDIT_RETENTION_DAYS=90
EOF

rm -f /opt/openvdi/OpenVDI/.env.migrate
chmod 600 /opt/openvdi/OpenVDI/.env
chown openvdi:openvdi /opt/openvdi/OpenVDI/.env
```

The full env-var reference (with descriptions and defaults) lives in
`/opt/openvdi/OpenVDI/.env.example` and `docs/deploy.md` →
*Environment Variables*.

### 7.3 Smoke-test the broker manually

```bash
sudo -u openvdi -i
cd /opt/openvdi/OpenVDI/broker
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8080 --log-config log_config.json
```

In another terminal:

```bash
curl -s http://127.0.0.1:8080/health
# → {"status":"ok"}
```

Watch the broker's stderr — you should see startup lines like
"providers loaded", "workers started", and (when an admin tries to
log in) "MCP authenticated as ..." (lazy login). If you see LDAP
errors at first login attempt, fix `OPENVDI_LDAP_*` and restart.

`Ctrl-C` to stop. Now switch to running it under systemd.

### 7.4 Install the systemd unit

```bash
sudo tee /etc/systemd/system/openvdi-broker.service > /dev/null <<'EOF'
[Unit]
Description=OpenVDI broker
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=openvdi
Group=openvdi
WorkingDirectory=/opt/openvdi/OpenVDI/broker
EnvironmentFile=/opt/openvdi/OpenVDI/.env
ExecStart=/opt/openvdi/OpenVDI/broker/.venv/bin/uvicorn \
    app.main:app --host 127.0.0.1 --port 8080
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/openvdi/broker.log
StandardError=append:/var/log/openvdi/broker.log

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now openvdi-broker
sudo systemctl status openvdi-broker
```

Verify:

```bash
curl -s http://127.0.0.1:8080/health   # → {"status":"ok"}
sudo journalctl -u openvdi-broker -n 50 --no-pager
```

---

## Stage 8 — Build and deploy the portal

### 8.1 Build the portal

```bash
sudo -u openvdi -i
cd /opt/openvdi/OpenVDI/portal
pnpm install
pnpm build
```

This produces `portal/dist/` — a tree of static HTML/JS/CSS that
Caddy will serve. The build may take 1–2 minutes.

### 8.2 Stage the dist

Caddy will read directly from `portal/dist`, so no copy is required.
Just make sure the path is readable by the `caddy` user (it is —
`/opt/openvdi/OpenVDI/portal/dist` is world-readable by default).

If you prefer a stable staged copy (so re-deploys don't ever serve
half-written files):

```bash
sudo mkdir -p /var/lib/openvdi/portal
sudo cp -r /opt/openvdi/OpenVDI/portal/dist/. /var/lib/openvdi/portal/
sudo chown -R openvdi:openvdi /var/lib/openvdi/portal
```

Use whichever path you prefer in the Caddyfile below.

---

## Stage 9 — Configure Caddy + TLS

### 9.1 DNS

Point `openvdi.example.com` (your chosen DNS name) at the OpenVDI
host's public IP. Caddy will obtain a Let's Encrypt cert
automatically once the name resolves.

If you're behind a corporate firewall, use your own CA — see
`docs/deploy.md` → *TLS / HTTPS* for the manual-cert path.

### 9.2 Write the Caddyfile

```bash
sudo tee /etc/caddy/Caddyfile > /dev/null <<'EOF'
openvdi.example.com {
    encode gzip

    # Health probe (unauthenticated, plain payload)
    handle /health {
        reverse_proxy 127.0.0.1:8080
    }

    # Broker API
    handle /api/* {
        reverse_proxy 127.0.0.1:8080
    }

    # Portal SPA — serve dist files; fall through to index.html for
    # client-side routes like /admin/sessions.
    handle {
        root * /opt/openvdi/OpenVDI/portal/dist
        try_files {path} /index.html
        file_server
    }
}
EOF

sudo systemctl reload caddy
sudo systemctl status caddy
```

### 9.3 Verify

From your laptop:

```bash
curl -sI https://openvdi.example.com/health
# Expect: HTTP/2 200, content-type: application/json
```

```bash
curl -s https://openvdi.example.com/health
# Expect: {"status":"ok"}
```

```bash
open https://openvdi.example.com/    # or just browse there
```

You should see the OpenVDI portal login page over HTTPS with a
green padlock.

If the cert is still pending (Caddy fetches Let's Encrypt on first
request), wait a few seconds and try again. Caddy logs cert
acquisition to its journal:

```bash
sudo journalctl -u caddy -f
```

---

## Stage 10 — First admin login

Browse to `https://openvdi.example.com/`.

Log in with **your AD username and password** (the user you added
to `OpenVDI-Admins` in Stage 3.1).

You should land on the OpenVDI home page with an admin sidebar
visible. The first thing the admin dashboard shows is the cluster
list — empty because we haven't registered any.

If login fails with "Invalid credentials":
- Check that the user is a direct member of `OpenVDI-Admins` (not
  via nested groups — v0 doesn't traverse).
- Tail the broker log: `sudo journalctl -u openvdi-broker -f` and
  retry. LDAP errors are logged at WARNING / ERROR.
- If you see `bind failure`, the broker bind account credentials
  are wrong; fix `OPENVDI_LDAP_BIND_*` and restart.

---

## Stage 11 — Register the first cluster

Still in the portal, as the admin user:

1. **Admin → Clusters → New cluster**.
2. Fill in:
   - **Name:** `dev-cluster` (matches Stage 1 plan)
   - **Provider type:** `proxmox`
   - **API URL:** `https://10.0.0.2:8006` (your PVE)
   - **Token ID:** `openvdi@pve!openvdi` (from Stage 2.1)
   - **Token secret:** the UUID PVE printed
   - **Verify SSL:** `false` for self-signed PVE cert; `true` if
     you have a real CA chain on your PVE.
3. Click **Save**.

The broker pings PVE in the background. Within ~30 seconds the
cluster's status flips from `pending` to `active` (or `offline` if
the connection fails — check the broker log).

Verify:

```bash
curl -s https://openvdi.example.com/api/v1/clusters \
  -H "Authorization: Bearer <your-portal-jwt>" | jq .
```

(Easier path: just refresh the cluster list page in the portal.)

---

## Stage 12 — Register the first template

You need the VMID of the template you built in Stage 4.

1. **Admin → Templates → New template**.
2. Fill in:
   - **Name:** `dev-win11-template` (whatever you want)
   - **Cluster:** the cluster you just registered.
   - **PVE VMID:** the VMID of your template.
   - **PVE node:** the node where the template lives.
   - **OS type:** `windows11` (or `ubuntu24`, etc.)
   - **CPU cores / memory / disk:** defaults; pools can override.
3. Click **Save**.

The broker validates the template by querying PVE for the VMID. If
it returns "VM not found" or "VM is not a template," double-check
that you converted the VM to a template in Stage 4.5.

---

## Stage 13 — Create the first pool

1. **Admin → Pools → New pool**.
2. Fill in:
   - **Name:** `engineering` (URL-safe slug)
   - **Display name:** `Engineering`
   - **Pool type:** `nonpersistent` (refreshes desktop on logoff —
     simplest for first test) or `persistent` (user keeps the same
     desktop across sessions).
   - **Cluster + template:** what you just registered.
   - **VMID range start / end:** `5000–5099` (Stage 1).
   - **Name prefix:** `ENG`.
   - **min_spare:** `1` (warm-spare count).
   - **max_size:** `5`.
3. Click **Save**.

### 13.1 Grant entitlements

In the pool's detail page:

1. Click **Entitlements**.
2. **Add entitlement** → user or group → name.
3. For your first smoke test, grant your own AD username (NOT the
   admin user — use a normal user that's NOT in `OpenVDI-Admins`,
   to verify entitlements work).

### 13.2 Provision warm spares

The pool was created empty. To pre-build the warm-spare desktop:

1. Pool detail page → **Provision**.
2. Count: `1`.
3. Click **Provision** and wait — the broker clones the template
   into the first VMID in the range. Watch the pool's "desktops"
   list; you'll see one row in `provisioning` status, then
   `available` after ~30–90 seconds (depends on storage).

If it sits in `provisioning` for more than a few minutes:

- `sudo journalctl -u openvdi-broker -f` — look for `pool_provisioner`
  worker errors.
- Common causes: VMID range conflicts, template lock contention,
  storage full.

The desktop is "ready" once `status=available` AND
`power_state=running`. The VM is started but no user is connected.

---

## Stage 14 — End-user logon test

Open a **private browser window** (so you log in fresh, as a
non-admin user this time).

1. Browse to `https://openvdi.example.com/`.
2. Log in with the **non-admin AD user** that you entitled in
   Stage 13.1.
3. You should see a "Desktops" page listing the pool you entitled
   the user to.
4. Click **Connect**.

The browser will:
- Trigger `POST /api/v1/me/desktops/<pool>/connect` — broker assigns
  a desktop and issues a Proxmox VNC ticket.
- Open the noVNC viewer, connecting `wss://<pve-node>:8006/...` directly.

**First-time PVE cert trust:** the noVNC connection goes from your
browser straight to the PVE node, not through Caddy. If your PVE has
a self-signed cert, the browser silently fails to connect. Visit
`https://<pve-node>:8006/` once and accept the cert manually, then
retry the OpenVDI logon.

Once connected, you should see the Windows / Linux desktop login
screen. Log in with your guest-OS credentials. The MS Windows
template will join the AD domain via your existing OS-level config
— that's not OpenVDI's concern.

When you click **Disconnect** in the portal, the session ends. For
non-persistent pools, the desktop refreshes on logoff (rolls back to
the `openvdi-base` snapshot taken right after first provisioning).

**That's the v0 acceptance.** Anything beyond this is operational
polish.

---

## Stage 15 (optional) — Set up the openvdi-admin MCP

For AI-agent integration (Praxova IT Agent, Claude Desktop, Claude
Code), follow `docs/mcp.md` and `docs/deploy.md` → *MCP Server
Deployment*. The MCP runs on the agent's host (operator laptop,
agent platform), not on the broker host — so this stage doesn't
apply to most "set up the server" workflows.

If you want a quick sanity check that the MCP works against your
new broker:

```bash
cd /opt/openvdi/OpenVDI/mcp/openvdi-admin
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

export OPENVDI_BROKER_URL=https://openvdi.example.com
export OPENVDI_SERVICE_USER=openvdi-mcp-svc
export OPENVDI_SERVICE_PASSWORD=<password>
export OPENVDI_VERIFY_SSL=true

python -m openvdi_admin.server
# Hangs waiting for MCP-protocol input on stdin. Ctrl-C to exit.
```

If it starts without errors, the broker is reachable and the
service-account credentials work. The acceptance script under
`mcp/openvdi-admin/scripts/acceptance.sh` walks the full M5 catalog
end-to-end (pre-provision, smoke, diagnose, reset) — useful for
verifying the install and as a regression gate.

---

## Stage 16 (optional) — Backup

OpenVDI's source of truth is its Postgres database. Set up a nightly
dump:

```bash
sudo tee /etc/cron.d/openvdi-pgdump > /dev/null <<'EOF'
0 2 * * *  postgres  pg_dump -Fc openvdi > /var/backups/openvdi-$(date +\%Y\%m\%d).dump && find /var/backups/ -name "openvdi-*.dump" -mtime +14 -delete
EOF
```

**ALSO back up — separately from the DB dump:**

- `OPENVDI_ENCRYPTION_KEY` (`/opt/openvdi/OpenVDI/.env` line). Without
  it, the DB dump's `clusters.token_secret` ciphertext is unreadable.
- `OPENVDI_JWT_SECRET` (same file). Without it, you re-issue all
  tokens after restore.

Put both into your secrets manager (1Password, sops, AWS Secrets
Manager, ...) — NOT alongside the dumps. The full backup/restore
procedure is in `docs/deploy.md` → *Backup and Recovery*.

---

## Day-2 operations

### Updating OpenVDI

```bash
sudo -u openvdi -i
cd /opt/openvdi/OpenVDI
git pull
cd broker
source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head     # idempotent; safe on every deploy
exit

cd /opt/openvdi/OpenVDI/portal
pnpm install
pnpm build

sudo systemctl restart openvdi-broker
sudo systemctl reload caddy        # only needed if portal/dist moved
```

`alembic upgrade head` is idempotent — safe to run on every deploy
even when there are no new migrations.

### Logs

| Component | Where |
|---|---|
| Broker (stderr → file) | `/var/log/openvdi/broker.log` |
| Broker (systemd journal) | `journalctl -u openvdi-broker` |
| Caddy | `journalctl -u caddy` |
| Postgres | `journalctl -u postgresql` |

With `OPENVDI_LOG_FORMAT=json`, the broker emits one JSON line per
request. Useful filters:

```bash
# All errors in the last hour
sudo journalctl -u openvdi-broker --since "1 hour ago" | jq 'select(.level=="ERROR")'

# Everything for one request_id (cross-system)
sudo journalctl -u openvdi-broker | grep <uuid>
```

### Adding more pools, templates, clusters

All via the admin dashboard — the same flow as Stages 11–13. Or via
the MCP if you've set it up.

### Adding a second broker for HA

The broker is stateless; multiple brokers behind a load balancer
self-elect for worker tasks via `pg_try_advisory_lock`. See
`docs/deploy.md` → *Multi-Broker* for the details.

---

## Troubleshooting

### "Login fails with no error in the broker log"

The portal won't log in if the cookie can't round-trip — that's the
same-origin requirement (Stage 1). Check:

- The portal and broker are reachable at the same DNS name and port.
- `OPENVDI_PORTAL_ORIGIN` matches what you typed in the browser.
- The browser is sending the refresh cookie back on `/api/v1/auth/refresh`.

If you're sure same-origin is set up correctly, browse to
`https://openvdi.example.com/api/v1/health` — that should return
`{"status":"ok"}` directly (no auth needed).

### "Provisioned desktops sit in `provisioning` forever"

The provisioner worker schedules the clone task with PVE; the
task_tracker worker polls for completion. If the task isn't
finishing:

- Check PVE for the actual clone task: `pvesh get /cluster/tasks
  --typefilter qmclone`. If the task failed, it'll be there with an
  exit status.
- Common: storage out of space, VMID out of range, template lock
  held by another operation.
- LVM lock orphans (Stage 2.3 mitigates). If you see
  `can't lock file` in PVE syslog, run the manual cleanup:
  `rm -f /run/lock/lvm/P_*`.

### "noVNC connection fails / black screen"

The browser connects directly to PVE — not through Caddy.

- Visit `https://<pve-node>:8006/` once and accept the cert (Stage
  14 note).
- Confirm the user's network can reach the PVE node on port 8006.
- The PVE token must include `VM.Console` (Stage 2.1) — without it,
  ticket issuance succeeds but PVE rejects the WebSocket.

### "Desktops never go to `available` (stuck in `provisioning`) but the PVE clone finished"

The session_monitor worker polls the guest agent. If the guest agent
isn't installed in the template (Stage 4.3), the desktop boots but
never reports ready. Check inside the desktop VM:

- Windows: `sc query QEMU-GA` — should be RUNNING.
- Linux: `systemctl status qemu-guest-agent`.

### "Drain takes forever / never completes"

Drain is one-way — the broker transitions a pool from `active` to
`draining` and stops there. It does NOT auto-flip to `disabled`.
If you want the pool gone, follow drain with delete-pool. See
`docs/mcp.md` → *Troubleshooting* for the full explanation.

### "Caddy can't get a cert"

Let's Encrypt requires the public DNS to resolve to your host AND
port 80 reachable from Let's Encrypt's verifier. If you're behind a
corporate firewall or running an internal-only deployment, switch to
a corporate CA cert and configure Caddy to use it manually — see
`docs/deploy.md` → *TLS / HTTPS*.

### "Postgres connection refused"

```bash
sudo systemctl status postgresql
sudo -u postgres psql -c '\l'
```

Make sure `pg_hba.conf` allows local TCP connections from `127.0.0.1`
with `md5` or `scram-sha-256` auth, and that Postgres is bound to
`127.0.0.1` (not just the unix socket). On Debian 12 the default is
fine; on older systems you may need to edit `pg_hba.conf` and
`postgresql.conf` and restart.

---

## Next steps

You have a working OpenVDI instance. From here:

- **Add more pools and templates.** Different OSes, different
  hardware specs, different entitled groups. The admin dashboard
  flow is the same.
- **Stand up the MCP.** `docs/mcp.md`. Useful even for solo
  operators — Claude Desktop becomes a fast diagnostic interface.
- **Plan for HA.** Add a second broker host behind Caddy with a
  shared Postgres. See `docs/deploy.md` → *Multi-Broker*.
- **Tune backups.** Stage 16 ships a baseline; consider WAL
  archiving for point-in-time recovery if your downtime tolerance
  is low.
- **Roadmap watch.** The `docs/implementation-plan.md` log tracks
  what's shipped and what's M6+. v1 swaps noVNC for KasmVNC for
  WAN-quality display; v2 adds vSphere/Hyper-V providers when a
  customer or validation partner materializes.

---

## See also

- `docs/architecture.md` — system layering, design philosophy.
- `docs/deploy.md` — production decisions, env vars, multi-broker,
  backup, monitoring.
- `docs/mcp.md` — operating OpenVDI from AI agents.
- `docs/api-design.md` — REST API surface (consumed by the portal
  and the MCP).
- `docs/database-schema.md` — the data model.
- `docs/providers/proxmox.md` — Proxmox provider implementation
  details and operational quirks (LVM lock cleanup, kebab-case
  params, etc.).
- `docs/session-tracking.md` — session lifecycle and guest-agent
  polling.
- `broker/README.md` — broker dev quickstart, Alembic migration
  reference.
- `portal/README.md` — portal dev quickstart, Praxova design system
  notes.
- `mcp/openvdi-admin/README.md` — MCP package developer reference.
