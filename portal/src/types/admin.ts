/**
 * Admin-side TypeScript types. Mirrors the broker's M2 admin schemas
 * field-for-field. Updated incrementally across M4-18 through M4-24
 * as each prompt consumes new endpoints.
 *
 * For each interface, the broker schema is the source of truth — if
 * the broker shape changes, this file updates in lockstep.
 */
import type { PoolStatus, PoolType } from "./desktops";

// ── Cluster ─────────────────────────────────────────────────


/** Mirror of `app.models.cluster.ClusterStatus`. */
export type ClusterStatus =
  | "pending"
  | "active"
  | "maintenance"
  | "offline";


/**
 * Mirror of `app.schemas.cluster.ClusterRead`. The broker NEVER
 * returns the token_secret field — write-only by design.
 */
export interface ClusterRead {
  id: string;
  name: string;
  provider_type: string;
  api_url: string;
  token_id: string;
  verify_ssl: boolean;
  node_filter: string | null;
  provider_config: Record<string, unknown>;
  status: ClusterStatus;
  created_at: string;
  updated_at: string;
}


/**
 * Mirror of `app.schemas.cluster.ClusterCreate`. The broker validates
 * credentials by calling `provider.ping()` post-insert; submission
 * blocks until that completes (typically 1-2 seconds).
 */
export interface ClusterCreate {
  name: string;
  /** Defaults to "proxmox" on the broker side; v0 portal never sends
      anything else. Field included for forward-compat. */
  provider_type?: string;
  api_url: string;
  token_id: string;
  token_secret: string;
  verify_ssl?: boolean;
  node_filter?: string | null;
  provider_config?: Record<string, unknown>;
}


/**
 * Mirror of `app.schemas.cluster.ClusterUpdate`. All fields optional;
 * omitted keys are not modified on the broker side.
 *
 * Per FE8: token_secret is omitted (key not present in JSON) to
 * preserve the existing value. The form code special-cases empty
 * input by deleting the key from the payload.
 */
export interface ClusterUpdate {
  name?: string;
  provider_type?: string;
  api_url?: string;
  token_id?: string;
  token_secret?: string;
  verify_ssl?: boolean;
  node_filter?: string | null;
  provider_config?: Record<string, unknown>;
}


// ── Template ────────────────────────────────────────────────


/**
 * Mirror of `app.schemas.template.TemplateRead`.
 *
 * `os_type` is a plain string — M2 deliberately deferred enum
 * narrowing. The form uses a select with the documented values
 * (windows11, windows10, ubuntu24, rhel9) but the wire type stays
 * permissive. M5+ may narrow on both sides simultaneously.
 */
export interface TemplateRead {
  id: string;
  cluster_id: string;
  name: string;
  pve_vmid: number;
  pve_node: string;
  os_type: string;
  description: string | null;
  cpu_cores: number;
  memory_mb: number;
  disk_gb: number;
  gpu_required: boolean;
  tags: unknown[];
  provider_config: Record<string, unknown>;
  status: string;
  created_at: string;
  updated_at: string;
}


/** Mirror of `app.schemas.template.TemplateCreate`. */
export interface TemplateCreate {
  cluster_id: string;
  name: string;
  pve_vmid: number;
  pve_node: string;
  os_type: string;
  description?: string | null;
  cpu_cores?: number;
  memory_mb?: number;
  disk_gb?: number;
  gpu_required?: boolean;
  tags?: unknown[];
  provider_config?: Record<string, unknown>;
}


/**
 * Mirror of `app.schemas.template.TemplateUpdate`. All fields
 * optional; omitted keys preserve existing values.
 *
 * Excludes cluster_id and pve_vmid — both are immutable post-creation
 * (the (cluster, vmid) pair is the cross-cluster uniqueness key). The
 * broker's PUT endpoint enforces this; the portal hides them.
 */
export interface TemplateUpdate {
  name?: string;
  pve_node?: string;
  os_type?: string;
  description?: string | null;
  cpu_cores?: number;
  memory_mb?: number;
  disk_gb?: number;
  gpu_required?: boolean;
  tags?: unknown[];
  provider_config?: Record<string, unknown>;
}


/** Mirror of `app.schemas.template.ValidationCheck`. */
export interface ValidationCheck {
  name: string; // "exists", "is_template", "agent_configured", etc.
  passed: boolean;
  message: string;
}


/** Mirror of `app.schemas.template.TemplateValidationResult`. */
export interface TemplateValidationResult {
  template_id: string;
  passed: boolean;
  checks: ValidationCheck[];
}


/**
 * UI-only enum of supported os_type values. The broker stores plain
 * string; this list is the form dropdown source. M5+ may narrow the
 * wire type and consolidate.
 */
export const OS_TYPES = [
  { value: "windows11", label: "Windows 11" },
  { value: "windows10", label: "Windows 10" },
  { value: "ubuntu24",  label: "Ubuntu 24.04" },
  { value: "rhel9",     label: "RHEL 9" },
] as const;


// ── Dashboard summary ─────────────────────────────────────────


export interface ResourceStatusCounts {
  total: number;
  by_status: Record<string, number>;
}


export interface PoolSummaryCounts {
  total: number;
  by_status: Record<PoolStatus, number>;
  by_type: Record<PoolType, number>;
}


export interface SessionSummaryCounts {
  total: number;
  active: number;
  connecting: number;
  disconnected: number;
  ended: number;
}


export interface CapacitySummary {
  total_vmid_slots: number;
  total_desktops: number;
}


export interface DashboardSummary {
  clusters: ResourceStatusCounts;
  pools: PoolSummaryCounts;
  desktops: ResourceStatusCounts;
  sessions: SessionSummaryCounts;
  capacity: CapacitySummary;
}


// ── Audit ────────────────────────────────────────────────────


/**
 * One row from the broker's audit_log. Mirrors `AuditRead`.
 *
 * `details` is JSONB — typed as `Record<string, unknown> | null`
 * because the shape varies per action; consumers cast on a per-action
 * basis when they need to read specific fields.
 */
export interface AuditEntry {
  id: number;
  timestamp: string;
  actor: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  details: Record<string, unknown> | null;
  client_ip: string | null;
}
