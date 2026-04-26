// =============================================================================
// OpenVDI Windows 11 Template — Variable Declarations
// =============================================================================
// All variables have sensible defaults for the pia-dev lab. Override in
// credentials.auto.pkrvars.hcl (auto-loaded) or via -var/-var-file flags.

// -----------------------------------------------------------------------------
// Proxmox Connection
// -----------------------------------------------------------------------------

variable "proxmox_url" {
  type        = string
  description = "Proxmox API URL (e.g. https://10.0.0.2:8006/api2/json)"
  default     = "https://10.0.0.2:8006/api2/json"
}

variable "proxmox_username" {
  type        = string
  description = "Proxmox API token name in user@realm!tokenid form"
  default     = "tofu@pve!automation"
}

variable "proxmox_token" {
  type        = string
  description = "Proxmox API token UUID (no username prefix)"
  sensitive   = true
}

variable "proxmox_node" {
  type        = string
  description = "Proxmox node to build on"
  default     = "pia-dev"
}

// -----------------------------------------------------------------------------
// VM Identity
// -----------------------------------------------------------------------------

variable "vm_id" {
  type        = number
  description = "Proxmox VM ID for the resulting template (9000 series convention)"
  default     = 9100
}

variable "vm_name" {
  type        = string
  description = "Display name for the template VM"
  default     = "openvdi-win11-template"
}

// -----------------------------------------------------------------------------
// Storage & ISOs
// -----------------------------------------------------------------------------

variable "vm_storage_pool" {
  type        = string
  description = "Proxmox storage pool for the VM disk (LVM-thin recommended)"
  default     = "local-lvm"
}

variable "iso_storage_pool" {
  type        = string
  description = "Proxmox storage where ISOs live"
  default     = "local"
}

variable "windows_iso" {
  type        = string
  description = "Path to the Windows 11 installation ISO (in Proxmox storage syntax)"
  default     = "local:iso/Win11_24H2_English_x64.iso"
}

variable "virtio_iso" {
  type        = string
  description = "Path to the VirtIO drivers ISO"
  default     = "local:iso/virtio-win-0.1.285.iso"
}

variable "autounattend_iso" {
  type        = string
  description = "Path to the pre-built autounattend ISO (built by build-answer-iso.sh)"
  default     = "local:iso/openvdi-win11-autounattend.iso"
}

// -----------------------------------------------------------------------------
// Build-Time Network
// -----------------------------------------------------------------------------
// These are used ONLY during the Packer build — sysprep wipes them at the end
// so clones boot with DHCP. Must not collide with any production IP.

variable "build_ip" {
  type        = string
  description = "Static IP assigned by autounattend so Packer can reach WinRM"
  default     = "10.0.0.250"
}

variable "build_netmask_bits" {
  type        = number
  description = "Subnet prefix length for the build IP"
  default     = 24
}

variable "build_gateway" {
  type        = string
  description = "Default gateway during build"
  default     = "10.0.0.1"
}

variable "build_dns" {
  type        = string
  description = "DNS server during build (only needed if you enable Windows Update)"
  default     = "10.0.0.1"
}

// -----------------------------------------------------------------------------
// WinRM Credentials (Build-Time Only)
// -----------------------------------------------------------------------------
// These match the password baked into autounattend.xml. They are wiped by
// sysprep generalize at the end of the build. The CLONE password is set
// separately by sysprep-unattend.xml and is documented in the README.

variable "winrm_username" {
  type    = string
  default = "Administrator"
}

variable "winrm_password" {
  type      = string
  default   = "P@cker-Bu1ld!"
  sensitive = true
}

// -----------------------------------------------------------------------------
// VM Hardware
// -----------------------------------------------------------------------------

variable "vm_cores" {
  type        = number
  description = "vCPU cores for the build VM (clones can override)"
  default     = 4
}

variable "vm_memory_mb" {
  type        = number
  description = "RAM in MB for the build VM"
  default     = 8192
}

variable "vm_disk_size_gb" {
  type        = number
  description = "Disk size for the template (clones inherit this)"
  default     = 64
}
