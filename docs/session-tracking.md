# OpenVDI Session Tracking & Monitoring

## Overview

Session tracking operates at three layers, from coarse to precise. The MVP implements Layers 1 and 2. Layer 3 is a future enhancement requiring a custom in-VM agent.

## Layer 1 — Broker-Level Tracking

**Always available. No agent required.**

OpenVDI knows when it brokered a connection (session record created) and when the user explicitly disconnects through the portal. This is reliable for "who was given access to what."

Tracked via the `sessions` table: `connecting → active → disconnected → ended`.

## Layer 2 — QEMU Guest Agent Polling

**Requires qemu-guest-agent installed in the VM template. This is a mandatory template requirement.**

The QEMU Guest Agent is a daemon running inside the VM that communicates with the host via VirtIO serial. Proxmox exposes guest agent commands through its REST API. No custom software needed — it ships with Proxmox's VirtIO drivers.

### Key Guest Agent Endpoints

| Proxmox API Endpoint | What It Tells Us |
|---|---|
| `GET /nodes/{node}/qemu/{vmid}/agent/get-users` | Who is logged into the OS right now (username + login time) |
| `GET /nodes/{node}/qemu/{vmid}/agent/get-osinfo` | OS version, kernel, hostname |
| `GET /nodes/{node}/qemu/{vmid}/agent/network-get-interfaces` | In-VM IP addresses |
| `GET /nodes/{node}/qemu/{vmid}/agent/get-fsinfo` | Disk/filesystem usage |
| `POST /nodes/{node}/qemu/{vmid}/agent/ping` | Agent alive check |
| `POST /nodes/{node}/qemu/{vmid}/agent/exec` | Run arbitrary commands inside VM |

### Session Monitor Background Worker

Runs every 15 seconds. Polls all desktops with active or recent sessions.

```
Every 15 seconds:
    For each desktop with status IN ('assigned', 'connected', 'disconnected'):

        1. Get VM power state from Proxmox
           GET /nodes/{node}/qemu/{vmid}/status/current
           → power_state = running | stopped | paused

        2. If power_state != 'running':
           → Mark session as 'ended'
           → Update desktop status appropriately
           → Skip remaining checks

        3. Poll guest agent for logged-in users
           GET /nodes/{node}/qemu/{vmid}/agent/get-users

           → If agent unreachable:
               • Set last_heartbeat = null
               • Continue (agent may not be started yet)

           → If no users logged in AND session was 'active':
               • User has logged off the OS
               • If pool.delete_on_logoff:
                   - Shutdown VM
                   - Destroy VM after stopped
                   - Remove desktop record
                   - Pool provisioner will replace if below min_spare
               • If pool.refresh_on_logoff:
                   - Shutdown VM via guest agent
                   - Wait for power_state = stopped
                   - Rollback to 'openvdi-base' snapshot
                   - Start VM
                   - Mark desktop as 'available'
               • Else:
                   - Mark session as 'ended'
                   - Mark desktop as 'available'

           → If user logged in:
               • Update session.os_user, session.last_heartbeat
               • If os_user doesn't match assigned_user: LOG WARNING
               • If session.status == 'connecting': promote to 'active'

        4. Poll network info (every 60 seconds, not every 15)
           GET /nodes/{node}/qemu/{vmid}/agent/network-get-interfaces
           → Update session.vm_ip_address
```

## Snapshot Model

Two distinct things have been called "snapshots" in prior drafts. To avoid the confusion that occurred in Milestone 1, this section is explicit:

**Templates do not have OpenVDI-managed snapshots.** The clone operation does not reference any snapshot on the template. Cloning from a Proxmox template uses the template's current disk state as the base, and Proxmox automatically creates a linked clone. See `architecture.md` → *Cloning Model* and `providers/proxmox.md` → *VM Lifecycle / clone_vm*.

**Individual desktops in non-persistent pools have a single snapshot named `openvdi-base`.** This snapshot is taken on the cloned desktop VM after initial provisioning (first boot, customization, graceful shutdown). It represents the clean baseline state for that desktop. The refresh-on-logoff cycle rolls the desktop back to this snapshot.

**Persistent pool desktops do not need the `openvdi-base` snapshot.** They are never rolled back — user changes persist across sessions by design.

A cloned desktop's snapshot list therefore looks like:

```
Non-persistent desktop (e.g. NONPERS-001):
    openvdi-base  ← created post-provisioning, used for rollback on logoff
    current       ← Proxmox's synthetic entry representing live state

Persistent desktop (e.g. ENG-003):
    current       ← only synthetic entry; no named snapshots
```

### Non-Persistent Pool Refresh Cycle

```
User logs off (detected by agent/get-users returning empty)
    │
    ├─ refresh_on_logoff = true?
    │   ├─ POST /nodes/{node}/qemu/{vmid}/agent/shutdown (graceful)
    │   ├─ Wait for power_state == stopped
    │   ├─ POST /nodes/{node}/qemu/{vmid}/snapshot/openvdi-base/rollback
    │   ├─ POST /nodes/{node}/qemu/{vmid}/status/start
    │   ├─ Wait for agent ping
    │   └─ Mark desktop as 'available' (back in spare pool)
    │
    ├─ delete_on_logoff = true?
    │   ├─ POST /nodes/{node}/qemu/{vmid}/status/stop
    │   ├─ Wait for power_state == stopped
    │   ├─ DELETE /nodes/{node}/qemu/{vmid}
    │   ├─ Remove desktop record from DB
    │   └─ Pool provisioner creates replacement if below min_spare
    │
    └─ Neither?
        ├─ Mark session as 'ended'
        └─ Mark desktop as 'available', leave VM running
```

### Non-Persistent Pool Provisioning Cycle

When the pool provisioner clones a new desktop for a non-persistent pool, the `openvdi-base` snapshot does not exist yet — the provisioner creates it as the final step:

```
Pool provisioner detects available_count < min_spare:
    │
    ├─ Allocate next VMID from pool range
    │
    ├─ Clone from template (NO snapname, NO full=True)
    │   POST /nodes/{node}/qemu/{template}/clone
    │     body: { newid: allocated_vmid, name: "PREFIX-NNN",
    │             pool: pool.pve_pool_id }
    │   → returns UPID; wait_for_task (timeout=600s)
    │   (Linked clone is automatic because source is a template.)
    │
    ├─ Apply VM overrides if pool specifies them (cpu_cores, memory_mb)
    │   POST /nodes/{node}/qemu/{vmid}/config
    │
    ├─ Start VM
    │   POST /nodes/{node}/qemu/{vmid}/status/start
    ├─ Wait for guest agent to respond
    │   POST /nodes/{node}/qemu/{vmid}/agent/ping (retry loop, ~60s cap)
    │
    ├─ Optional: run customization via agent/exec
    │   (hostname, domain join, sysprep resealing, etc.)
    │
    ├─ Graceful shutdown via guest agent
    │   POST /nodes/{node}/qemu/{vmid}/status/shutdown
    │     body: { timeout: 120, forceStop: 1 }
    ├─ Wait for power_state == stopped
    │
    ├─ Create the openvdi-base snapshot (ON THE DESKTOP, not the template)
    │   POST /nodes/{node}/qemu/{vmid}/snapshot
    │     body: { snapname: "openvdi-base",
    │             description: "OpenVDI clean baseline" }
    ├─ Wait for snapshot task
    │
    ├─ Start VM again
    │   POST /nodes/{node}/qemu/{vmid}/status/start
    ├─ Wait for agent
    │
    └─ Mark desktop as 'available'
```

### Persistent Pool Provisioning Cycle

Simpler — no baseline snapshot needed:

```
Broker detects a user entitled to persistent pool has no assigned desktop:
    │
    ├─ Allocate next VMID from pool range
    ├─ Clone from template (same call as non-persistent)
    ├─ Wait for clone task (timeout=600s)
    ├─ Apply VM overrides if any
    ├─ Record assignment (desktops.assigned_user = username)
    ├─ Start VM
    ├─ Wait for agent ping
    └─ Mark desktop as 'assigned' → proceed to issue VNC ticket
```

## Layer 3 — OpenVDI In-VM Agent (Future)

For Horizon-class session intelligence beyond what the QEMU guest agent provides:

- **Idle detection:** Mouse/keyboard activity timestamps. "User logged in but idle for 45 minutes."
- **Application inventory:** List of running processes/apps. Useful for license compliance.
- **Performance metrics:** CPU, RAM, disk I/O from the user's perspective (not hypervisor-level).
- **Display protocol status:** Connected/disconnected at the protocol level (noVNC, KasmVNC).
- **Printer/USB redirection:** What peripherals are redirected (future).
- **Multi-monitor detection:** Screen count and resolution.

This would be a lightweight service (Python or Go) installed in the VM template that:
- Communicates with the OpenVDI broker via HTTPS API or WebSocket
- Publishes telemetry to `session_metrics` table
- Is signed and versioned, updated via the broker
- Runs as a Windows service or systemd unit

**Not needed for MVP.** Layers 1+2 provide sufficient admin visibility.

## Pool Provisioner Background Worker

Runs every 30 seconds. Ensures non-persistent pools maintain warm spares.

```
Every 30 seconds:
    For each pool with status == 'active' AND pool_type == 'nonpersistent':

        available_count = COUNT desktops WHERE pool_id = pool.id
                          AND status = 'available'
        total_count = COUNT desktops WHERE pool_id = pool.id
                      AND status NOT IN ('deleting', 'error')

        if available_count < pool.min_spare AND total_count < pool.max_size:
            needed = min(pool.min_spare - available_count,
                        pool.max_size - total_count)

            for i in range(needed):
                allocate VMID
                create desktop record (status='provisioning')
                submit clone task to provisioning queue
```

## Health Checker Background Worker

Runs every 60 seconds. Monitors cluster and desktop health.

```
Every 60 seconds:
    For each cluster with status == 'active':

        1. Ping Proxmox API
           GET /version
           → If unreachable: set cluster.status = 'offline', alert

        2. Get node status
           GET /nodes
           → Update node availability
           → Flag desktops on unreachable nodes

        3. Check storage capacity
           GET /nodes/{node}/storage
           → Warn if any VDI storage < 20% free

        4. Reconcile desktop state
           GET /nodes/{node}/qemu (list all VMs)
           → Flag desktops in DB that don't exist in Proxmox
           → Flag Proxmox VMs with openvdi tags not in DB (orphans)

        5. Check for desktops stuck in 'provisioning' > 10 minutes
           → Mark as 'error', log details
```

## Task Tracker Background Worker

Runs every 5 seconds. Monitors in-flight Proxmox async tasks.

```
Every 5 seconds:
    For each desktop with pve_task_upid IS NOT NULL:

        GET /nodes/{node}/tasks/{upid}/status

        if task.status == 'stopped':
            if task.exitstatus == 'OK':
                → Update desktop status based on what the task was
                  (clone → available, start → update power_state, etc.)
            else:
                → Mark desktop as 'error'
                → Store error message from exitstatus

            Clear pve_task_upid
```

Note: `exitstatus` is returned by Proxmox but is not documented in the OpenAPI spec. See `providers/proxmox.md` → *Task Tracking* for the full quirk note.

## Template Requirements

For session tracking to work, VM templates MUST have:

1. **QEMU Guest Agent installed and enabled**
   - Linux: `apt install qemu-guest-agent && systemctl enable qemu-guest-agent`
   - Windows: Install VirtIO drivers (includes guest agent service)

2. **Guest agent enabled in VM config**
   - `agent: 1` in Proxmox VM config
   - Verified during `POST /templates/{id}/validate`

3. **VirtIO serial port configured** (required for guest agent communication)
   - Proxmox adds this automatically when agent=1

4. **Network connectivity for noVNC** (VM must be reachable from browser for v1+ with KasmVNC)

5. **VM converted to a template** (`qm template <vmid>` or `--template 1` at create time)
   - Non-template VMs cannot be linked-cloned from; Proxmox forces full clones
   - Template status is verified during `POST /templates/{id}/validate`
