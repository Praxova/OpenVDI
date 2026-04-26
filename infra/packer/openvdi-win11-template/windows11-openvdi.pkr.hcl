// =============================================================================
// OpenVDI Windows 11 Template — Main Build Definition
// =============================================================================
// Builds a Windows 11 gold image suitable for the OpenVDI broker's M2-19 path:
//   1. Linked-clone from this template
//   2. Boot, wait for qemu-guest-agent (90s timeout)
//   3. 60s quiesce, then snapshot 'openvdi-base'
//   4. Restart, wait for agent again
//   5. Issue noVNC console ticket
//
// The two non-obvious requirements:
//   - qemu-guest-agent MUST be installed and set to start automatically
//   - OOBE on clones MUST complete within ~60s of agent-up, or the snapshot
//     captures a half-booted state and clones break consistently
//
// See README.md for the full design rationale.

packer {
  required_plugins {
    proxmox = {
      version = ">= 1.2.0"
      source  = "github.com/hashicorp/proxmox"
    }
  }
}

// -----------------------------------------------------------------------------
// Source: VM Build Configuration
// -----------------------------------------------------------------------------

source "proxmox-iso" "openvdi_win11" {
  // --- Connection ------------------------------------------------------------
  proxmox_url              = var.proxmox_url
  username                 = var.proxmox_username
  token                    = var.proxmox_token
  node                     = var.proxmox_node
  insecure_skip_tls_verify = true

  // --- VM Identity -----------------------------------------------------------
  vm_id                = var.vm_id
  vm_name              = var.vm_name
  template_description = "OpenVDI Windows 11 gold image. Built ${formatdate("YYYY-MM-DD hh:mm", timestamp())} UTC."

  // --- Hardware --------------------------------------------------------------
  // SeaBIOS + i440fx is the proven-working path on Proxmox for Win11 with
  // TPM bypass registry keys (set in autounattend WinPE pass). OVMF/UEFI is
  // theoretically more "correct" for Win11 but boot ordering with SATA
  // controllers has been unreliable in your lab. SeaBIOS Just Works.
  bios     = "seabios"
  machine  = "pc"
  cpu_type = "host"
  cores    = var.vm_cores
  sockets  = 1
  memory   = var.vm_memory_mb

  // --- Disk ------------------------------------------------------------------
  // IDE during install — VirtIO storage drivers don't exist in the Windows
  // installer's WinPE environment until they're loaded. We could load them
  // via the VirtIO ISO and a F6 driver step, but IDE works without that
  // complexity. The provisioner installs VirtIO drivers post-boot.
  scsi_controller = "virtio-scsi-pci"

  disks {
    type         = "ide"
    storage_pool = var.vm_storage_pool
    disk_size    = "${var.vm_disk_size_gb}G"
    cache_mode   = "writeback"
    discard      = true
    format       = "raw"
  }

  // --- Network ---------------------------------------------------------------
  // e1000 is Windows-native (no driver pre-load required). VirtIO network
  // is faster but needs the VirtIO ISO plugged in during install. e1000
  // makes the build path simpler. Performance is fine for a VDI desktop.
  network_adapters {
    bridge   = "vmbr0"
    model    = "e1000"
    firewall = false
  }

  // --- ISOs ------------------------------------------------------------------
  // Three ISOs mounted:
  //   - boot_iso: Windows 11 installer (boot from this)
  //   - virtio_iso: VirtIO drivers (loaded by provisioner post-install)
  //   - autounattend_iso: contains autounattend.xml with OEMDRV label so
  //     Windows Setup auto-detects it
  boot_iso {
    type         = "ide"
    iso_file     = var.windows_iso
    unmount      = true
    iso_checksum = "none"
  }

  additional_iso_files {
    type         = "ide"
    iso_file     = var.virtio_iso
    unmount      = true
    iso_checksum = "none"
  }

  additional_iso_files {
    type         = "ide"
    iso_file     = var.autounattend_iso
    unmount      = true
    iso_checksum = "none"
  }

  // --- Boot Behavior ---------------------------------------------------------
  // Windows installer prompts "Press any key to boot from CD or DVD" once.
  // <enter> handles that. The autounattend ISO's OEMDRV label means Setup
  // finds the answer file automatically — no boot_command magic needed.
  boot_wait    = "10s"
  boot_command = ["<enter>"]

  // --- Guest Agent -----------------------------------------------------------
  // Tells Proxmox to expose the qemu-guest-agent endpoints. The agent itself
  // is installed inside the VM by the install-virtio.ps1 provisioner. Both
  // halves (this flag + in-VM install) are required for the broker's
  // agent_ping to succeed on clones.
  qemu_agent = true

  // --- Cloud-Init ------------------------------------------------------------
  // Not used for Windows. Proxmox cloud-init is Linux-focused.
  cloud_init = false

  // --- WinRM Communicator ----------------------------------------------------
  // Packer connects via WinRM at the static IP set by autounattend's
  // FirstLogonCommands. winrm_use_ssl=false because Windows generates a
  // self-signed cert that Packer would have to trust; HTTP is fine for a
  // build environment.
  communicator   = "winrm"
  winrm_username = var.winrm_username
  winrm_password = var.winrm_password
  winrm_use_ssl  = false
  winrm_insecure = true
  winrm_port     = 5985
  winrm_host     = var.build_ip
  winrm_timeout  = "30m"
}

// -----------------------------------------------------------------------------
// Build: Provisioner Pipeline
// -----------------------------------------------------------------------------

build {
  name    = "openvdi-win11"
  sources = ["source.proxmox-iso.openvdi_win11"]

  // Sanity check — confirm we connected to the right OS
  provisioner "powershell" {
    inline = [
      "Write-Host '=== Validating Windows install ==='",
      "$os = Get-CimInstance Win32_OperatingSystem",
      "Write-Host \"OS:    $($os.Caption)\"",
      "Write-Host \"Build: $($os.BuildNumber)\"",
      "Write-Host \"Arch:  $($os.OSArchitecture)\"",
      "if ($os.BuildNumber -lt 22000) { throw 'Not Windows 11 — aborting' }"
    ]
  }

  // Install VirtIO drivers AND qemu-guest-agent in one shot.
  // The VirtIO ISO ships with a single guest-tools installer that handles
  // both — far simpler than pnputil-ing each driver individually.
  provisioner "powershell" {
    script = "scripts/install-virtio.ps1"
  }

  // Reboot so the freshly-installed VirtIO services are running.
  provisioner "windows-restart" {
    restart_timeout = "10m"
  }

  // Verify the QEMU Guest Agent service is running.
  // If this fails, clones will fail M2-19 at agent_ping. Better to find out
  // here than after building.
  provisioner "powershell" {
    inline = [
      "Write-Host '=== Verifying QEMU Guest Agent ==='",
      "$svc = Get-Service -Name 'QEMU-GA' -ErrorAction SilentlyContinue",
      "if (-not $svc) { throw 'QEMU-GA service not found — VirtIO install failed' }",
      "Write-Host \"Service status: $($svc.Status)\"",
      "Write-Host \"Startup type:   $($svc.StartType)\"",
      "if ($svc.Status -ne 'Running') { throw 'QEMU-GA not running' }",
      "if ($svc.StartType -ne 'Automatic') { throw 'QEMU-GA must be Automatic, not $($svc.StartType)' }"
    ]
  }

  // Pre-sysprep cleanup — remove temp files, clear logs, defrag.
  // Happens BEFORE we stage the sysprep unattend file (otherwise cleanup
  // wipes it; this was a real bug in your earlier templates).
  provisioner "powershell" {
    script = "scripts/cleanup.ps1"
  }

  // Stage the sysprep answer file in the canonical location.
  // sysprep.exe with no /unattend flag picks this up automatically; passing
  // it explicitly is belt-and-suspenders.
  provisioner "file" {
    source      = "sysprep-unattend.xml"
    destination = "C:\\Windows\\System32\\Sysprep\\unattend.xml"
  }

  // Run sysprep generalize/oobe/shutdown.
  // /quiet prevents the GUI from popping up. The VM will power off when
  // sysprep finishes — Packer detects this and proceeds.
  provisioner "powershell" {
    inline = [
      "Write-Host '=== Running sysprep generalize ==='",
      "Write-Host 'VM will shut down when sysprep completes...'",
      "& C:\\Windows\\System32\\Sysprep\\sysprep.exe /generalize /oobe /shutdown /unattend:C:\\Windows\\System32\\Sysprep\\unattend.xml /quiet"
    ]
    // Don't wait for command to "complete" — sysprep ends with shutdown,
    // which kills WinRM. Packer's shutdown detection handles this.
    valid_exit_codes = [0, 16, -1, 2300218]
  }
}
