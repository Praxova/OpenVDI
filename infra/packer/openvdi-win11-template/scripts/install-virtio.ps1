# =============================================================================
# install-virtio.ps1
# =============================================================================
# Installs VirtIO drivers and the QEMU Guest Agent inside the Packer build VM.
# ASCII-only - no curly quotes, no em-dashes, no unicode.
# =============================================================================

$ErrorActionPreference = 'Stop'

Write-Host '=== VirtIO Drivers and QEMU Guest Agent Installation ==='

# Locate the VirtIO ISO drive
$virtioDrive = $null
$cdromDrives = Get-CimInstance -ClassName Win32_LogicalDisk | Where-Object { $_.DriveType -eq 5 }

foreach ($drive in $cdromDrives) {
    $letter = $drive.DeviceID
    if (Test-Path "$letter\virtio-win-guest-tools.exe") {
        $virtioDrive = $letter
        Write-Host "Found VirtIO ISO at $virtioDrive (label: $($drive.VolumeName))"
        break
    }
}

if (-not $virtioDrive) {
    throw "VirtIO ISO not found on any CD-ROM drive."
}

# Install the VirtIO guest tools
$installer = "$virtioDrive\virtio-win-guest-tools.exe"
Write-Host "Running $installer ..."

$proc = Start-Process -FilePath $installer `
    -ArgumentList '/install', '/quiet', '/norestart' `
    -Wait -PassThru -NoNewWindow

if ($proc.ExitCode -ne 0 -and $proc.ExitCode -ne 3010) {
    throw "virtio-win-guest-tools installer exited with code $($proc.ExitCode)"
}

Write-Host "VirtIO guest tools installer exit code: $($proc.ExitCode) (0 or 3010 = OK)"

# Verify the QEMU Guest Agent service exists
Write-Host '--- Verifying QEMU Guest Agent service ---'

$svc = Get-Service -Name 'QEMU-GA' -ErrorAction SilentlyContinue

if (-not $svc) {
    throw 'QEMU-GA service not found after installer ran.'
}

Write-Host "Service name:    $($svc.Name)"
Write-Host "Display name:    $($svc.DisplayName)"
Write-Host "Status:          $($svc.Status)"
Write-Host "Startup type:    $($svc.StartType)"

# Force startup type to Automatic
if ($svc.StartType -ne 'Automatic') {
    Write-Host 'Setting QEMU-GA startup type to Automatic...'
    Set-Service -Name 'QEMU-GA' -StartupType Automatic
}

# Try to start it now
if ($svc.Status -ne 'Running') {
    Write-Host 'Attempting to start QEMU-GA...'
    try {
        Start-Service -Name 'QEMU-GA' -ErrorAction Stop
        Write-Host 'QEMU-GA started successfully.'
    } catch {
        Write-Host 'QEMU-GA could not start yet - this is expected before the post-install reboot.'
        Write-Host "Reason: $($_.Exception.Message)"
    }
}

Write-Host '=== VirtIO and QEMU Guest Agent installation complete ==='
