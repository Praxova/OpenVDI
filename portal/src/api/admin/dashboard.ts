import { useQuery } from "@tanstack/react-query";

import { useBrokerClient } from "@/api/client";
import type {
  AuditEntry,
  ClusterRead,
  DashboardSummary,
} from "@/types/admin";

/**
 * Admin query keys. Per FE3: rooted at ["admin", ...] to namespace
 * away from user-facing keys. Each admin prompt adds keys for its
 * resource; M4-18 adds the dashboard / clusters / audit shared roots.
 */
export const adminKeys = {
  all: ["admin"] as const,
  dashboard: ["admin", "dashboard"] as const,
  clusters: ["admin", "clusters"] as const,
  audit: ["admin", "audit"] as const,
};


/**
 * Fetch the dashboard summary aggregate. One request returns counts
 * for clusters, pools, desktops, sessions, and capacity.
 */
export function useDashboardSummaryQuery() {
  const client = useBrokerClient();
  return useQuery({
    queryKey: adminKeys.dashboard,
    queryFn: () => client.get<DashboardSummary>("/api/v1/dashboard/summary"),
  });
}


/**
 * Fetch all clusters. M4-19 will extend this with create/update/delete
 * mutations; M4-18 only consumes the list.
 */
export function useClustersQuery() {
  const client = useBrokerClient();
  return useQuery({
    queryKey: adminKeys.clusters,
    queryFn: () => client.get<ClusterRead[]>("/api/v1/clusters"),
  });
}


/**
 * Fetch the most recent audit events. The dashboard card shows the
 * last N — passes `?limit=N` to the broker. The broker's /audit
 * endpoint defaults to timestamp-desc when no sort is specified, so
 * the response is naturally most-recent-first.
 *
 * M4-24's audit page extends this with filter parameters.
 */
export function useRecentAuditQuery(limit: number = 10) {
  const client = useBrokerClient();
  return useQuery({
    queryKey: [...adminKeys.audit, "recent", limit] as const,
    queryFn: () =>
      client.get<AuditEntry[]>(`/api/v1/audit?limit=${limit}`),
  });
}
