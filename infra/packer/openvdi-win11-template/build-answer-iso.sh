#!/usr/bin/env bash
# =============================================================================
# build-answer-iso.sh
# =============================================================================
# Builds an ISO containing autounattend.xml with the OEMDRV volume label, then
# uploads it to the Proxmox host so the Packer template can mount it during
# install.
#
# Why an ISO instead of Packer's built-in cd_files?
#   Packer's cd_files / cd_content options work, but Windows PE isn't always
#   reliable about scanning all attached drives for the answer file. Using an
#   ISO labeled OEMDRV is the documented Microsoft mechanism — Setup
#   specifically looks for that label and uses any autounattend.xml it finds
#   on the volume. This is the pattern your WS2022 template uses too.
# =============================================================================

set -euo pipefail

# -----------------------------------------------------------------------------
# Configuration — override via environment if needed
# -----------------------------------------------------------------------------
PROXMOX_HOST="${PROXMOX_HOST:-10.0.0.2}"
PROXMOX_USER="${PROXMOX_USER:-root}"
PROXMOX_ISO_PATH="${PROXMOX_ISO_PATH:-/var/lib/vz/template/iso}"
ISO_NAME="${ISO_NAME:-openvdi-win11-autounattend.iso}"

# -----------------------------------------------------------------------------
# Locate the answer file relative to this script
# -----------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANSWER_FILE="${SCRIPT_DIR}/autounattend.xml"
WORK_DIR="$(mktemp -d)"
LOCAL_ISO="${SCRIPT_DIR}/${ISO_NAME}"

# Cleanup tempdir on exit
trap 'rm -rf "${WORK_DIR}"' EXIT

# -----------------------------------------------------------------------------
# Sanity checks
# -----------------------------------------------------------------------------
if [[ ! -f "${ANSWER_FILE}" ]]; then
    echo "ERROR: autounattend.xml not found at ${ANSWER_FILE}" >&2
    exit 1
fi

if ! command -v genisoimage &>/dev/null; then
    echo "ERROR: genisoimage not installed. Run: sudo apt install genisoimage" >&2
    exit 1
fi

# -----------------------------------------------------------------------------
# Stage files into the work dir
# -----------------------------------------------------------------------------
echo "[1/3] Staging files..."
cp "${ANSWER_FILE}" "${WORK_DIR}/autounattend.xml"

# -----------------------------------------------------------------------------
# Build the ISO
#   -V OEMDRV       : volume label that Windows Setup looks for
#   -J              : Joliet (Windows-friendly long names)
#   -r              : Rock Ridge (Linux-friendly attributes)
#   -iso-level 4    : allow Windows-style filenames
# -----------------------------------------------------------------------------
echo "[2/3] Building ISO at ${LOCAL_ISO}..."
genisoimage \
    -V OEMDRV \
    -J -r -iso-level 4 \
    -o "${LOCAL_ISO}" \
    "${WORK_DIR}"

ls -lh "${LOCAL_ISO}"

# -----------------------------------------------------------------------------
# Upload to Proxmox via SCP
# -----------------------------------------------------------------------------
echo "[3/3] Uploading to ${PROXMOX_USER}@${PROXMOX_HOST}:${PROXMOX_ISO_PATH}/..."
scp -o BatchMode=yes "${LOCAL_ISO}" "${PROXMOX_USER}@${PROXMOX_HOST}:${PROXMOX_ISO_PATH}/${ISO_NAME}"

echo ""
echo "Done. The Packer template references this ISO as:"
echo "  local:iso/${ISO_NAME}"
