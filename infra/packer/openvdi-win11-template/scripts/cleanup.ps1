# =============================================================================
# cleanup.ps1
# =============================================================================
# Pre-sysprep cleanup. Reduces template size and removes build-only artifacts
# so clones boot clean.
#
# CRITICAL ORDERING NOTE:
#   This runs BEFORE sysprep-unattend.xml is staged at C:\Windows\System32\
#   Sysprep\unattend.xml. The Packer pipeline does it in that order on
#   purpose — your earlier templates had a bug where cleanup ran AFTER the
#   answer file was placed, and the temp-file wipe deleted Panther\unattend
#   before Windows could read it.
# =============================================================================

$ErrorActionPreference = 'Continue'   # Cleanup is best-effort; don't bail on failures

Write-Host '=== Pre-Sysprep Cleanup ==='

# -----------------------------------------------------------------------------
# Stop services that hold file locks on stuff we want to clean
# -----------------------------------------------------------------------------
Write-Host '--- Stopping services that lock cleanup targets ---'
$servicesToStop = @('wuauserv', 'bits', 'TrustedInstaller')
foreach ($svc in $servicesToStop) {
    try {
        Stop-Service -Name $svc -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped: $svc"
    } catch {
        Write-Host "Could not stop $svc (may not be running): $($_.Exception.Message)"
    }
}

# -----------------------------------------------------------------------------
# Wipe Windows Update download cache
# -----------------------------------------------------------------------------
# Even with no Windows Update during build, this directory accumulates cruft.
Write-Host '--- Cleaning Windows Update cache ---'
$wuPath = 'C:\Windows\SoftwareDistribution\Download'
if (Test-Path $wuPath) {
    Get-ChildItem -Path $wuPath -Recurse -Force -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Cleaned: $wuPath"
}

# -----------------------------------------------------------------------------
# Wipe temp directories
# -----------------------------------------------------------------------------
Write-Host '--- Cleaning temp directories ---'
$tempPaths = @(
    'C:\Windows\Temp',
    "$env:LOCALAPPDATA\Temp",
    'C:\Users\Administrator\AppData\Local\Temp'
)
foreach ($path in $tempPaths) {
    if (Test-Path $path) {
        Get-ChildItem -Path $path -Recurse -Force -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "Cleaned: $path"
    }
}

# -----------------------------------------------------------------------------
# Wipe Panther directory (Windows Setup logs)
# -----------------------------------------------------------------------------
# The unattend.xml from the autounattend ISO is left here after install. We
# clear it so it doesn't conflict with the sysprep unattend that gets staged
# AFTER this script runs.
Write-Host '--- Cleaning Panther directory ---'
$panther = 'C:\Windows\Panther'
if (Test-Path $panther) {
    # Don't delete the whole directory — sysprep needs it to exist. Just clear
    # contents.
    Get-ChildItem -Path $panther -Recurse -Force -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Cleaned: $panther"
}

# -----------------------------------------------------------------------------
# Clear event logs
# -----------------------------------------------------------------------------
# Reduces template size and gives clones a clean log slate.
Write-Host '--- Clearing event logs ---'
Get-WinEvent -ListLog * -ErrorAction SilentlyContinue |
    Where-Object { $_.RecordCount -gt 0 -and $_.IsEnabled } |
    ForEach-Object {
        try {
            [System.Diagnostics.Eventing.Reader.EventLogSession]::GlobalSession.ClearLog($_.LogName)
        } catch {
            # Some logs can't be cleared (Security needs special perms); skip
        }
    }
Write-Host 'Event logs cleared.'

# -----------------------------------------------------------------------------
# Defrag (or trim, if SSD)
# -----------------------------------------------------------------------------
# On thin-provisioned LVM, this isn't strictly necessary, but it doesn't hurt.
# Optimize-Volume detects the underlying storage type and does the right thing
# (TRIM on SSD, defrag on HDD).
Write-Host '--- Optimizing C: volume ---'
try {
    Optimize-Volume -DriveLetter C -ReTrim -Verbose -ErrorAction SilentlyContinue
} catch {
    Write-Host "Optimize-Volume failed (non-fatal): $($_.Exception.Message)"
}

# -----------------------------------------------------------------------------
# DISM cleanup — reduces WinSxS bloat
# -----------------------------------------------------------------------------
# This is the biggest size win. Removes superseded component versions.
Write-Host '--- DISM component cleanup (this takes a few minutes) ---'
& dism.exe /online /cleanup-image /startcomponentcleanup /resetbase
if ($LASTEXITCODE -ne 0) {
    Write-Host "DISM cleanup returned $LASTEXITCODE (non-fatal)"
}

Write-Host '=== Cleanup complete. Sysprep is next. ==='
