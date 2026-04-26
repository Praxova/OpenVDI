# OpenVDI — Windows 11 Gold Image (Proxmox)

Packer template for building Windows 11 desktop images consumed by the OpenVDI broker on Proxmox. This template is **purpose-built for OpenVDI's M2-19 path** and intentionally diverges from the IT Agent / VDI templates in the sister project — read the "Why this template is different" section before treating it as a drop-in replacement.

---

## Why This Template Is Different

If you already have a Win11 Packer template (e.g. the vSphere/Horizon one in your Praxova lab), you might be tempted to just point Proxmox at that and call it done. Don't. OpenVDI's broker has a very specific contract with the gold image that other VDI platforms (Horizon, Citrix, AVD) handle differently:

### The OpenVDI contract

The M2-19 provisioning path in the broker does this on every clone:

1. **Linked clone** from this template (Proxmox API call, no in-VM activity)
2. **Boot the clone**
3. **Wait up to 90 seconds** for `qemu-guest-agent` to respond to `agent_ping`
4. **Sleep 60 seconds** ("M2-07a quiesce window")
5. **Take snapshot `openvdi-base`** — this is the snapshot every user session starts from
6. **Reboot the clone**
7. **Wait for agent again**
8. Issue noVNC console ticket → done

The broker **never logs in** to the clone, **never injects an unattend file**, **never runs PowerShell remotely**, and **never knows the clone's IP**. Everything that needs to happen on a clone has to happen autonomously, driven only by what's in the gold image.

### What this means for the template

Two requirements dominate the design:

**Requirement 1: `qemu-guest-agent` must respond within 90s of boot.**

This drives:
- VirtIO drivers + QEMU Guest Agent baked into the template (`scripts/install-virtio.ps1`)
- Service startup type forced to `Automatic`
- Proxmox VM config has `agent: 1` (the `qemu_agent = true` line in the Packer template)

**Requirement 2: OOBE must complete within ~60s of agent-up, or clones break.**

This is the subtle one. The agent service starts during early Windows boot, before user-mode shell is ready, so `agent_ping` typically succeeds at ~30-60s. The 60s quiesce ends at ~90-120s. **If OOBE is still running when the snapshot is taken, the snapshot captures a half-booted state, and every clone restored from `openvdi-base` will be broken in the same way.**

This drives:
- Maximally aggressive OOBE skip flags in `sysprep-unattend.xml`
- No FirstLogonCommands beyond a marker file
- No domain join, no app installs, no user creation in OOBE
- The template is sysprep'd `/generalize /oobe /shutdown` and **never booted again** before being converted to a Proxmox template

### What this means for things this template is NOT

- **Not domain-joined.** OpenVDI handles that in M3+ via the broker, not at template-bake time.
- **No applications.** Apps would extend OOBE, the very thing we're optimizing against. Layer apps in via App Volumes / FSLogix / a derived "apps" template later.
- **No WinRM scaffolding for post-clone configuration.** The broker doesn't use it, and exposing WinRM on every desktop is a security smell anyway.
- **No static IP for clones.** Clones use DHCP. The broker doesn't need to reach in.

If you need any of the above for a different VDI use case, build a different template — don't bolt features onto this one.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Build pipeline (you, manually)                          │
│                                                          │
│  ./build-answer-iso.sh   ──upload──▶  Proxmox local:iso/│
│        │                                                 │
│        ▼                                                 │
│  packer build .                                          │
│        │                                                 │
│        ▼                                                 │
│  Proxmox: VM 9001 (template)                             │
└──────────────────────────┬──────────────────────────────┘
                           │
                           │ OpenVDI broker uses this template
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Per user session (M2-19 path)                           │
│                                                          │
│  Linked clone ──▶ Boot ──▶ Wait for agent ──▶ Quiesce   │
│       ──▶ Snapshot openvdi-base ──▶ Reboot ──▶ noVNC    │
└─────────────────────────────────────────────────────────┘
```

---

## Hardware Choices (and why)

| Choice | Value | Reason |
|---|---|---|
| BIOS | SeaBIOS | OVMF/UEFI boot ordering with SATA has been flaky in your lab. SeaBIOS + TPM bypass works reliably. |
| Disk bus | IDE (during install), VirtIO-SCSI (after) | Windows installer has no native VirtIO storage driver, but does have native IDE. We install VirtIO drivers post-boot, then the template can be cloned with whichever bus the broker prefers. |
| NIC | e1000 | Native Windows driver. VirtIO net is faster but needs F6 driver loading during install. e1000 is fine for VDI desktop traffic. |
| Disk size | 64 GB | Win11 minimum is 64 GB. Larger sizes are easy to extend later but hard to shrink. |
| RAM | 8 GB | Comfortable for the build. The clone size in OpenVDI pool definitions can override this. |
| Cores | 4 | Enough for fast install + sysprep. Clone size is set by the OpenVDI pool. |
| TPM | None | SeaBIOS doesn't support vTPM. Win11 thinks it's running on "unsupported" hardware but functions normally. The TPM bypass registry keys in `autounattend.xml` are what allow install in this configuration. |

---

## Repository Layout

```
windows11-proxmox/
├── README.md                         <-- you are here
├── windows11-openvdi.pkr.hcl         <-- main Packer template
├── variables.pkr.hcl                  <-- variable declarations + defaults
├── credentials.auto.pkrvars.hcl.example  <-- copy to credentials.auto.pkrvars.hcl
├── autounattend.xml                  <-- BUILD-PHASE Windows answer file
├── sysprep-unattend.xml              <-- CLONE-PHASE OOBE answer file
├── build-answer-iso.sh               <-- bundles autounattend.xml into ISO
└── scripts/
    ├── install-virtio.ps1            <-- VirtIO drivers + QEMU GA
    └── cleanup.ps1                    <-- pre-sysprep cleanup
```

The two unattend files have different jobs and are easy to confuse — `autounattend.xml` runs once during the Packer build, `sysprep-unattend.xml` runs on every clone. Mixing them up will cause subtle, hard-to-debug failures.

---

## Prerequisites

**On your Linux workstation:**
- Packer ≥ 1.9
- `genisoimage` package (for `build-answer-iso.sh`)
- SSH access to Proxmox host with key-based auth (no password prompts)

**On Proxmox (`pia-dev`):**
- Win11 ISO uploaded to `local:iso/` — recommended: `Win11_24H2_English_x64.iso`
- VirtIO ISO uploaded to `local:iso/` — recommended: `virtio-win-0.1.285.iso` (or newer)
- API token `tofu@pve!automation` with VM create/clone/template permissions
- Sufficient `local-lvm` space for the 64 GB template

---

## How to Build

### 1. Set up credentials

```bash
cp credentials.auto.pkrvars.hcl.example credentials.auto.pkrvars.hcl
# edit credentials.auto.pkrvars.hcl — paste your Proxmox API token UUID
```

The `.auto.pkrvars.hcl` extension is auto-loaded by Packer. The file is gitignored.

### 2. Build and upload the autounattend ISO

```bash
./build-answer-iso.sh
```

This bundles `autounattend.xml` into an ISO with the `OEMDRV` volume label and SCPs it to Proxmox at `local:iso/openvdi-win11-autounattend.iso`.

Re-run this whenever you change `autounattend.xml`.

### 3. Run the Packer build

```bash
packer init .
packer validate .
packer build .

# Verbose mode for debugging:
PACKER_LOG=1 packer build . 2>&1 | tee packer-build.log
```

Build time: roughly 15–25 minutes (no Windows Update — that's intentional).

### 4. Verify the template

After build, on Proxmox:

```bash
qm config 9001 | grep -E '^(template|agent|name):'
```

Expected output:
```
agent: 1
name: openvdi-win11-template
template: 1
```

All three lines must be present. If `agent: 1` is missing, OpenVDI's M2-19 path will fail at the validation step (Step 4 in the broker spec).

### 5. Smoke test before handing off to OpenVDI

Before relying on the template for OpenVDI, sanity-check that a clone boots and the guest agent comes up in time:

```bash
# On Proxmox host:
qm clone 9001 999 --name smoke-test --full 0   # linked clone
qm start 999

# Wait ~30 seconds, then:
qm agent 999 ping && echo "AGENT OK" || echo "AGENT FAILED"

# Should print AGENT OK within 90 seconds of starting.
```

If the agent doesn't come up within 90s, OpenVDI's M2-19 will fail with `"VM started but guest agent unresponsive"`. Investigate before shipping.

Cleanup:
```bash
qm stop 999
qm destroy 999
```

---

## Troubleshooting

### Build fails with "no route to host" or WinRM timeout

Almost always means the static IP didn't get applied during `FirstLogonCommands`. Check:

- VM has a working DHCP lease initially (Win11 needs network for some specialize-pass operations)
- `10.0.0.250` isn't in use by anything else on your network
- Open the VM console in Proxmox during the build — you'll see the auto-logon happen, then Windows will be at the desktop with a CMD window briefly visible running each FirstLogonCommand

### "QEMU-GA service not found" during build

VirtIO installer didn't run. Check:

- VirtIO ISO is mounted (look at VM hardware in Proxmox Web UI during build)
- `virtio-win-guest-tools.exe` exists on the VirtIO ISO root (older ISOs may have it under a subdirectory — adjust `install-virtio.ps1` accordingly)

### Clone boots but agent never responds

The most likely culprit is OOBE not completing. To diagnose:

1. Clone the template manually: `qm clone 9001 999 --full 0 && qm start 999`
2. Open noVNC in Proxmox Web UI immediately
3. Watch what's on screen at the 60s, 90s, 120s marks
4. If you see OOBE screens still running at 90s+, OOBE skip is not aggressive enough — review `sysprep-unattend.xml`

If you see OOBE complete and a login screen but the agent still doesn't ping, the QEMU-GA service is not starting. Boot the clone, log in as Administrator (`OpenVDI@Default1`), and check `Get-Service QEMU-GA`.

### Clones consistently fail M2-19 in production but smoke test passes

This is the timing-on-the-edge case described in the design notes. Options in order of preference:

1. **Reduce OOBE work further.** Remove `RegisteredOrganization` / `RegisteredOwner` if present, set timezone to `UTC` not a longer name, etc.
2. **Ask OpenVDI to make `M2-07a` quiesce time configurable** and bump it to 120s. This is a broker change, not a template change.
3. **Delay the QEMU-GA service** with a small startup script that sleeps before starting it. This shifts the 90s window but risks the broker timing out waiting for agent.

Try them in that order. Only get into option 3 if option 1 doesn't get you out of the danger zone.

---

## What's Next

This template handles M2-19. Future OpenVDI milestones will need additions:

- **M3 (portal + display):** Probably nothing template-side. The Proxmox noVNC console is what M3 uses.
- **M4+ (user identity / AD):** Either domain-join clones via post-clone PowerShell triggered by the broker, or use a separate domain-joined template variant.
- **Apps (when needed):** Don't add to this template. Build an "apps" template that clones from this one, installs apps, and re-syspreps. That keeps the base template lean and the OOBE-timing window safe.

When you start that work, mirror this directory: `windows11-proxmox-apps/` with its own README explaining what it adds on top of the base.

---

## Credentials Reference

| Context | Username | Password | Notes |
|---|---|---|---|
| Proxmox API | `tofu@pve!automation` | UUID in `credentials.auto.pkrvars.hcl` | Reused from your existing setup |
| Build-time WinRM | `Administrator` | `P@cker-Bu1ld!` | Only during Packer build. Wiped by sysprep. |
| Clone Administrator | `Administrator` | `OpenVDI@Default1` | **Change before going to production.** Hardcoded in the template image. |

⚠️ `OpenVDI@Default1` is on every clone built from this template. Anyone with a noVNC console ticket can use it. For lab use this is fine; for anything user-facing, the broker should rotate this credential per assignment.
