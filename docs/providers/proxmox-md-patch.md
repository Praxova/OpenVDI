# Patch for /home/alton/projects/OpenVDI/docs/providers/proxmox.md

Insert the following new subsection **between the existing HTTP-status-to-exception mapping table and the `### Retry Policy` heading** (in the current file, between lines 334 and 336 — after the `| 4xx (non-auth) | ... |` row and before `### Retry Policy (internal to provider)`).

---

```markdown
### Known not-found detection gaps

Proxmox does not consistently use HTTP 404 for missing resources. Several endpoints return HTTP 500 with a body message like `"Configuration file does not exist"` or `"no such VM"` when the caller referenced a VMID that doesn't exist on the targeted node. The `_ProxmoxClient` currently routes these through the generic `5xx → ProxmoxError` path (which surfaces as `PROVIDER_ERROR` HTTP 502 at the API layer) rather than raising `ProviderNotFoundError` (HTTP 404).

This means admin-facing flows that could distinguish "you typed a wrong VMID" from "Proxmox is genuinely broken" currently can't — both surface as 502. The wire-contract envelope shape is correct in both cases; only the status code and `error.code` differ from the ideal.

**Known instances (M2):**

- `GET /nodes/{node}/qemu/{vmid}/status/current` when `{vmid}` doesn't exist — surfaces in `POST /api/v1/templates` validation flow when an operator types the wrong VMID.

Other endpoints are likely affected (snapshot reads, config reads, task status for missing UPIDs) but haven't been systematically catalogued.

**Why the provider doesn't currently fix this:** the policy in *Error surface for missing snapshots* above says the provider does not pattern-match on Proxmox error message strings — Proxmox's message wording is not an API stability guarantee, and substring matching would break silently on a Proxmox minor-version bump. That policy was written for *task* errors (asynchronous, surfaced via `wait_for_task`), but the same epistemic concern applies to synchronous 500s.

**Deferred to M4.** The principled fix is a narrow allowlist of HTTP 500 + body-fragment patterns that `_ProxmoxClient` recognizes as "actually 404" and reclassifies to `ProviderNotFoundError`. This is a provider-layer change that affects every caller of the affected endpoints, and it deserves to land alongside the conformance test suite (M4) so the allowlist is validated against a live cluster and the pattern set can be discovered systematically rather than one-endpoint-at-a-time.

Until then, admin-facing error messages on the affected flows carry the Proxmox literal error text ("Configuration file does not exist") which is enough for operator diagnosis even if the status code (502) is misleading about the category.
```

---

## How to apply

Use desktop-commander's `edit_block` against `/home/alton/projects/OpenVDI/docs/providers/proxmox.md`:

- **old_string**: the last row of the HTTP-mapping table plus the blank line plus the `### Retry Policy` heading — use enough context to make the match unique.
- **new_string**: the same three lines plus the new subsection inserted before the `### Retry Policy` heading.

Concretely:

```
old_string:
| 4xx (non-auth) | `ProxmoxError` — client bug or validation failure |

### Retry Policy (internal to provider)

new_string:
| 4xx (non-auth) | `ProxmoxError` — client bug or validation failure |

### Known not-found detection gaps

Proxmox does not consistently use HTTP 404 for missing resources. Several endpoints return HTTP 500 with a body message like `"Configuration file does not exist"` or `"no such VM"` when the caller referenced a VMID that doesn't exist on the targeted node. The `_ProxmoxClient` currently routes these through the generic `5xx → ProxmoxError` path (which surfaces as `PROVIDER_ERROR` HTTP 502 at the API layer) rather than raising `ProviderNotFoundError` (HTTP 404).

This means admin-facing flows that could distinguish "you typed a wrong VMID" from "Proxmox is genuinely broken" currently can't — both surface as 502. The wire-contract envelope shape is correct in both cases; only the status code and `error.code` differ from the ideal.

**Known instances (M2):**

- `GET /nodes/{node}/qemu/{vmid}/status/current` when `{vmid}` doesn't exist — surfaces in `POST /api/v1/templates` validation flow when an operator types the wrong VMID.

Other endpoints are likely affected (snapshot reads, config reads, task status for missing UPIDs) but haven't been systematically catalogued.

**Why the provider doesn't currently fix this:** the policy in *Error surface for missing snapshots* above says the provider does not pattern-match on Proxmox error message strings — Proxmox's message wording is not an API stability guarantee, and substring matching would break silently on a Proxmox minor-version bump. That policy was written for *task* errors (asynchronous, surfaced via `wait_for_task`), but the same epistemic concern applies to synchronous 500s.

**Deferred to M4.** The principled fix is a narrow allowlist of HTTP 500 + body-fragment patterns that `_ProxmoxClient` recognizes as "actually 404" and reclassifies to `ProviderNotFoundError`. This is a provider-layer change that affects every caller of the affected endpoints, and it deserves to land alongside the conformance test suite (M4) so the allowlist is validated against a live cluster and the pattern set can be discovered systematically rather than one-endpoint-at-a-time.

Until then, admin-facing error messages on the affected flows carry the Proxmox literal error text ("Configuration file does not exist") which is enough for operator diagnosis even if the status code (502) is misleading about the category.

### Retry Policy (internal to provider)
```

The `old_string` match is uniquely identified by the combination of the last table row + the `### Retry Policy` heading — no other place in the file has that pair.
