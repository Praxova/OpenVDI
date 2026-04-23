"""snake_case ↔ kebab-case translation for Proxmox API parameters.

Proxmox uses kebab-case or camelCase for some parameter names that don't
fit Python's snake_case convention. This module exists because Proxmox
silently ignores unknown parameters (no 400 error), so a typo or a missed
translation becomes a ghost bug.

Do NOT replace this with a generic `_` → `-` converter. That would
incorrectly translate `pve_vmid`, `exit_status`, etc.
"""
from __future__ import annotations

PARAM_MAP: dict[str, str] = {
    "generate_password": "generate-password",
    "input_data": "input-data",
    "target_node": "target",
    "force_stop": "forceStop",
    "keep_active": "keepActive",
    "skip_lock": "skiplock",
}


def translate_params(params: dict | None) -> dict:
    """Apply PARAM_MAP to keys; drop None values; never mutate input."""
    if not params:
        return {}
    out: dict = {}
    for k, v in params.items():
        if v is None:
            continue
        out[PARAM_MAP.get(k, k)] = v
    return out
