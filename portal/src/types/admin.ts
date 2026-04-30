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
