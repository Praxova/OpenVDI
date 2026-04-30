/**
 * Admin-side TypeScript types. Mirrors the broker's M2 admin schemas
 * field-for-field. Updated incrementally across M4-18 through M4-24
 * as each prompt consumes new endpoints.
 *
 * For each interface, the broker schema is the source of truth — if
 * the broker shape changes, this file updates in lockstep.
 */
import type { PoolStatus, PoolType } from "./desktops";
export type { PoolStatus, PoolType };

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


// ── Pool ────────────────────────────────────────────────────


/** Slug regex matches the broker's POOL_NAME_PATTERN. */
export const POOL_NAME_PATTERN = "^[a-z0-9][a-z0-9_-]*[a-z0-9]$|^[a-z0-9]$";


/** Mirror of `app.schemas.pool.PoolRead`. */
export interface PoolRead {
  id: string;
  name: string;
  display_name: string;
  description: string | null;
  pool_type: PoolType;
  template_id: string;
  cluster_id: string;
  min_spare: number;
  max_size: number;
  vmid_range_start: number;
  vmid_range_end: number;
  name_prefix: string;
  target_nodes: string | null;
  target_storage: string | null;
  cpu_cores: number | null;
  memory_mb: number | null;
  pve_pool_id: string | null;
  provider_config: Record<string, unknown>;
  auto_logoff_min: number;
  delete_on_logoff: boolean;
  refresh_on_logoff: boolean;
  status: PoolStatus;
  created_at: string;
  updated_at: string;
}


/** Mirror of `app.schemas.pool.PoolCreate`. */
export interface PoolCreate {
  name: string;
  display_name: string;
  description?: string | null;
  pool_type: PoolType;
  template_id: string;
  cluster_id: string;
  min_spare?: number;
  max_size?: number;
  vmid_range_start: number;
  vmid_range_end: number;
  name_prefix: string;
  target_nodes?: string | null;
  cpu_cores?: number | null;
  memory_mb?: number | null;
  auto_logoff_min?: number;
  delete_on_logoff?: boolean;
  refresh_on_logoff?: boolean;
}


/**
 * Mirror of `app.schemas.pool.PoolUpdate`. Per the schema docstring:
 * vmid_range_start, vmid_range_end, template_id, cluster_id, and
 * pool_type are immutable post-creation. The form renders those as
 * read-only inputs and excludes them from the PUT payload.
 *
 * `name_prefix` IS mutable in the broker schema (despite what M4-21's
 * design notes claim) — the field is exposed as editable here.
 */
export interface PoolUpdate {
  name?: string;
  display_name?: string;
  description?: string | null;
  min_spare?: number;
  max_size?: number;
  name_prefix?: string;
  target_nodes?: string | null;
  cpu_cores?: number | null;
  memory_mb?: number | null;
  auto_logoff_min?: number;
  delete_on_logoff?: boolean;
  refresh_on_logoff?: boolean;
  status?: PoolStatus;
}


/**
 * Mirror of `app.schemas.dashboard.PoolCapacityWithName` — only the
 * fields M4-21's list page renders. The broker's PoolCapacityDetail
 * additionally exposes per-status counts (provisioning/assigned/etc.);
 * a future per-pool detail page may consume them.
 */
export interface PoolCapacityRow {
  pool_id: string;
  pool_name: string;
  pool_display_name: string;
  pool_status: PoolStatus;
  pool_type: PoolType;
  range_capacity: number;
  total_desktops: number;
  free_slots: number;
  provisioning: number;
  available: number;
  assigned: number;
  connected: number;
  disconnected: number;
  error: number;
  deleting: number;
  maintenance: number;
}


// ── Entitlement ─────────────────────────────────────────────


export type PrincipalType = "user" | "group";


export interface EntitlementRead {
  id: string;
  pool_id: string;
  principal_type: string;
  principal_name: string;
  created_at: string;
}


export interface EntitlementCreate {
  principal_type: PrincipalType;
  principal_name: string;
}


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
