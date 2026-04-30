import { useQuery } from "@tanstack/react-query";

import { useBrokerClient } from "@/api/client";
import type { AuditRead } from "@/types/admin";


const auditKey = ["admin", "audit"] as const;


export interface AuditListFilters {
  actor?: string;
  /** Exact match, OR trailing '*' for prefix match (broker only
      supports suffix wildcard, not leading). */
  action?: string;
  resource_type?: string;
  /** UUID; caller validates client-side and omits when invalid. */
  resource_id?: string;
  since?: string; // ISO; computed from time preset
  offset: number;
  limit: number;
}


/**
 * Audit list query. No mutations — the table is append-only by design.
 * The broker defaults to timestamp-desc when no sort is specified, so
 * the response is naturally newest-first.
 */
export function useAuditQuery(filters: AuditListFilters) {
  const client = useBrokerClient();
  const params = new URLSearchParams();
  if (filters.actor) params.set("actor", filters.actor);
  if (filters.action) params.set("action", filters.action);
  if (filters.resource_type) {
    params.set("resource_type", filters.resource_type);
  }
  if (filters.resource_id) params.set("resource_id", filters.resource_id);
  if (filters.since) params.set("since", filters.since);
  params.set("limit", String(filters.limit));
  params.set("offset", String(filters.offset));
  return useQuery({
    queryKey: [...auditKey, filters] as const,
    queryFn: () =>
      client.get<AuditRead[]>(`/api/v1/audit?${params.toString()}`),
  });
}
